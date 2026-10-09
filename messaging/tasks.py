"""Celery tasks for OneXtel RCS message delivery, retry backoff, and refund processing."""
import json
import logging
import time
from typing import Any, Dict
import uuid
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from integrations.onextel.client import OneXtelClient, OneXtelClientError
from wallet.services import refund
from .models import Message


logger = logging.getLogger(__name__)


def build_onextel_content_message(message: Message) -> Dict[str, Any]:
    """Construct OneXtel contentMessage structure according to content_type."""
    c_type = message.content_type
    payload = message.payload or {}

    if c_type == Message.ContentType.TEXT:
        return {"text": payload.get("text", "")}

    if c_type == Message.ContentType.RICH_CARD:
        rich_card = payload.get("richCard") or payload
        return {"richCard": rich_card}

    if c_type == Message.ContentType.CAROUSEL:
        rich_card = payload.get("richCard") or payload
        return {"richCard": rich_card}

    if c_type == Message.ContentType.TEMPLATE:
        tpl_code = message.template.name if message.template else ""
        c_params = message.custom_params or {}
        custom_params_str = json.dumps(c_params) if isinstance(c_params, dict) else str(c_params)
        return {
            "templateMessage": {
                "templateCode": tpl_code,
                "customParams": custom_params_str,
            }
        }

    # Fallback to text if unknown
    return {"text": payload.get("text", "")}


def build_onextel_rcs_item(message: Message) -> Dict[str, Any]:
    """Assemble single RCS message item for OneXtel /send_sms API."""
    content_message = build_onextel_content_message(message)

    sender_name = message.sender_profile.sender_profile_name if message.sender_profile else ""

    item: Dict[str, Any] = {
        "messageType": message.message_type,
        "recipient": message.recipient,
        "sender": sender_name,
        "contentMessage": content_message,
    }

    if message.ttl is not None:
        item["ttl"] = f"{message.ttl}s"

    for i in range(1, 7):
        val = getattr(message, f"custref{i}", "")
        if val:
            item[f"custref{i}"] = val

    return item


def throttle_rate_limit():
    """Simple rate limiter honoring ONEXTEL_MAX_TPS."""
    max_tps = getattr(settings, "ONEXTEL_MAX_TPS", 100)
    if max_tps <= 0:
        return

    # Window timestamp in seconds
    current_sec = int(time.time())
    cache_key = f"onextel_tps_count_{current_sec}"
    try:
        count = cache.get_or_set(cache_key, 0, timeout=2)
        if count >= max_tps:
            # Sleep remainder of second to throttle smoothly
            time.sleep(0.05)
        cache.incr(cache_key)
    except Exception:
        # Pass silently in local dev if cache doesn't support atomic incr
        pass


@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def send_message_task(self, message_id: str):
    """Celery task to build OneXtel payload, call API, update status, and refund on permanent failure."""
    try:
        message = Message.objects.select_related("user", "sender_profile", "template").get(id=message_id)
    except Message.DoesNotExist:
        logger.error("send_message_task: Message %s not found.", message_id)
        return

    if message.status not in [Message.Status.QUEUED, Message.Status.FAILED]:
        logger.info("Message %s already in state %s. Skipping.", message_id, message.status)
        return

    throttle_rate_limit()

    client = OneXtelClient()
    rcs_item = build_onextel_rcs_item(message)
    url_shortener = message.payload.get("urlShortener", {})

    try:
        resp = client.send_message(
            rcs_item=rcs_item,
            prefix=message.prefix,
            url_shortener=url_shortener,
        )

        onextel_id = resp.get("messageId")
        message.onextel_message_id = onextel_id or ""
        message.status = Message.Status.SUBMITTED
        message.submitted_at = timezone.now()
        message.failure_reason = ""
        message.save(update_fields=["onextel_message_id", "status", "submitted_at", "failure_reason"])

        logger.info("Message %s successfully submitted to OneXtel with ID %s", message_id, onextel_id)
        return onextel_id

    except Exception as e:
        logger.warning(
            "Failed sending message %s on attempt %d: %s",
            message_id,
            self.request.retries + 1,
            e,
        )

        # Retry with exponential backoff if retries remain
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            raise self.retry(exc=e, countdown=countdown)

        # Final failure: mark message failed and refund user if not already refunded
        logger.error("Permanent failure for message %s after %d retries: %s", message_id, self.max_retries, e)
        message.status = Message.Status.FAILED
        message.failure_reason = str(e)

        if not message.is_refunded:
            try:
                refund(
                    user=message.user,
                    amount=message.cost,
                    remarks=f"Refund for failed RCS message {message.id}",
                    reference_type="MESSAGE_REFUND",
                    reference_id=str(message.id),
                )
                message.is_refunded = True
                logger.info("Refunded %s INR to user %s for failed message %s", message.cost, message.user.email, message.id)
            except Exception as ref_err:
                logger.critical("Failed to process refund for message %s: %s", message.id, ref_err)

        message.save(update_fields=["status", "failure_reason", "is_refunded"])
        raise e


# Status progression ranks to prevent backwards or out-of-order transitions
STATUS_RANK = {
    Message.Status.QUEUED: 10,
    Message.Status.SUBMITTED: 20,
    Message.Status.SENT: 30,
    Message.Status.DELIVERED: 40,
    Message.Status.READ: 50,
}


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def process_dlr_task(self, dlr_log_id: int):
    """Asynchronously process an ingested DLR webhook log.

    Guarantees:
        1. Idempotency (repeating the same DLR causes no duplicate state changes or refunds).
        2. Out-of-order tolerance (e.g. delivered will never be demoted back to submitted or sent).
        3. Single-refund safety (only refunds once on failed delivery via `is_refunded` flag).
    """
    from .dlr_parser import parse_dlr_payload
    from .models import DLRLog

    try:
        dlr_log = DLRLog.objects.get(id=dlr_log_id)
    except DLRLog.DoesNotExist:
        logger.error("process_dlr_task: DLRLog #%d not found.", dlr_log_id)
        return

    parsed = parse_dlr_payload(dlr_log.payload)
    onextel_id = parsed.get("onextel_message_id")
    new_status = parsed.get("status")
    failure_reason = parsed.get("failure_reason") or ""
    event_ts = parsed.get("event_timestamp") or timezone.now()

    dlr_log.onextel_message_id = onextel_id
    dlr_log.status_extracted = str(new_status or parsed.get("raw_status") or "")

    if not onextel_id:
        dlr_log.processed = True
        dlr_log.processed_at = timezone.now()
        dlr_log.error_message = "No onextel_message_id found in DLR payload."
        dlr_log.save(update_fields=["onextel_message_id", "status_extracted", "processed", "processed_at", "error_message"])
        return

    # Match corresponding Message
    message = Message.objects.select_related("user").filter(onextel_message_id=onextel_id).first()
    if not message:
        # Check by id if internal UUID was reported in reference
        try:
            val_uuid = uuid.UUID(str(onextel_id))
            message = Message.objects.select_related("user").filter(id=val_uuid).first()
        except (ValueError, TypeError, AttributeError):
            pass

    if not message:
        logger.warning("No message matched for OneXtel ID '%s'. Marking DLR pending reprocess.", onextel_id)
        dlr_log.error_message = f"Message with carrier ID '{onextel_id}' not found."
        dlr_log.save(update_fields=["onextel_message_id", "status_extracted", "error_message"])
        return

    dlr_log.message = message

    if not new_status:
        dlr_log.processed = True
        dlr_log.processed_at = timezone.now()
        dlr_log.error_message = f"Could not determine status from raw status: '{parsed.get('raw_status')}'."
        dlr_log.save(update_fields=["message", "processed", "processed_at", "error_message"])
        return

    current_status = message.status
    current_rank = STATUS_RANK.get(current_status, 0)
    new_rank = STATUS_RANK.get(new_status, 0)
    fields_to_update = []

    # 1. State: FAILED
    if new_status == Message.Status.FAILED:
        # Rule: Never transition from DELIVERED or READ to FAILED
        if current_status in [Message.Status.DELIVERED, Message.Status.READ]:
            logger.info("Ignoring FAILED DLR for message %s already in state %s", message.id, current_status)
        else:
            message.status = Message.Status.FAILED
            message.failure_reason = failure_reason or message.failure_reason
            fields_to_update.extend(["status", "failure_reason"])

            # Refund once guard
            if not message.is_refunded:
                try:
                    refund(
                        user=message.user,
                        amount=message.cost,
                        remarks=f"DLR refund for failed message {message.id}: {failure_reason}",
                        reference_type="MESSAGE_REFUND",
                        reference_id=str(message.id),
                    )
                    message.is_refunded = True
                    fields_to_update.append("is_refunded")
                    logger.info("Refunded %s to user %s for failed DLR on %s", message.cost, message.user.email, message.id)
                except Exception as ref_err:
                    logger.critical("Failed refunding message %s on DLR: %s", message.id, ref_err)

    # 2. State: DELIVERED or READ or SENT (Forward Progression)
    elif new_status in STATUS_RANK:
        if current_status == Message.Status.FAILED:
            logger.info("Ignoring state update for already FAILED message %s", message.id)
        elif new_rank >= current_rank:
            # Advance state
            message.status = new_status
            fields_to_update.append("status")

            if new_status == Message.Status.DELIVERED and not message.delivered_at:
                message.delivered_at = event_ts
                fields_to_update.append("delivered_at")

            elif new_status == Message.Status.READ:
                if not message.delivered_at:
                    message.delivered_at = event_ts
                    fields_to_update.append("delivered_at")
                if not message.read_at:
                    message.read_at = event_ts
                    fields_to_update.append("read_at")
        else:
            logger.info(
                "Out-of-order DLR ignored for message %s: current rank %d (%s) >= new rank %d (%s)",
                message.id,
                current_rank,
                current_status,
                new_rank,
                new_status,
            )

    if fields_to_update:
        message.save(update_fields=list(set(fields_to_update)))

    dlr_log.processed = True
    dlr_log.processed_at = timezone.now()
    dlr_log.error_message = ""
    dlr_log.save(update_fields=["message", "processed", "processed_at", "error_message"])
    logger.info("DLRLog #%d processed successfully for message %s (New status: %s)", dlr_log_id, message.id, message.status)

    # Forward status to client webhook callback if configured
    try:
        from api.tasks import forward_client_webhook_task
        forward_client_webhook_task.delay(str(message.id), message.status, message.failure_reason)
    except Exception as hook_err:
        logger.error("Failed triggering client webhook for message %s: %s", message.id, hook_err)
