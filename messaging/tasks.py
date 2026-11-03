"""Celery tasks for OneXtel RCS message delivery, retry backoff, and refund processing."""
import json
import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
import uuid
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
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
        tpl_dict: Dict[str, Any] = {"templateCode": tpl_code}
        c_params = message.custom_params
        if c_params and isinstance(c_params, dict) and any(c_params.values()):
            tpl_dict["customParams"] = json.dumps(c_params)
        elif isinstance(c_params, str) and c_params.strip() and c_params.strip() not in ["{}", "[]"]:
            tpl_dict["customParams"] = c_params
        return {"templateMessage": tpl_dict}

    # Fallback to text if unknown
    return {"text": payload.get("text", "")}


def build_onextel_rcs_item(message: Message) -> Dict[str, Any]:
    """Assemble single RCS message item for OneXtel /send_sms API."""
    content_message = build_onextel_content_message(message)

    sender_name = (
        message.sender_profile.sender_id
        or message.sender_profile.sender_profile_name
        if message.sender_profile
        else ""
    )

    # Normalize messageType to 'Promotional', 'Transactional', or 'OTP'
    m_raw = (message.message_type or "PROMOTIONAL").upper()
    if m_raw == "TRANSACTIONAL":
        m_type_formatted = "Transactional"
    elif m_raw == "OTP":
        m_type_formatted = "OTP"
    else:
        m_type_formatted = "Promotional"

    item: Dict[str, Any] = {
        "messageType": m_type_formatted,
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

    if not item.get("custref1"):
        item["custref1"] = str(message.id)

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


def extract_onextel_id(resp: Any) -> str:
    """Extract carrier message ID from a response entry or dict."""
    if not isinstance(resp, dict):
        if isinstance(resp, str):
            return resp.strip()
        return ""
    mid = (
        resp.get("messageId")
        or resp.get("message_id")
        or resp.get("msgId")
        or resp.get("id")
        or resp.get("refId")
        or ""
    )
    if not mid and isinstance(resp.get("data"), dict):
        mid = (
            resp["data"].get("messageId")
            or resp["data"].get("msgId")
            or resp["data"].get("id")
            or resp["data"].get("refId")
            or ""
        )
    return str(mid).strip()


def is_entry_success(entry: Any) -> bool:
    """Check if entry indicates success based on code (200) or status ('success')."""
    if isinstance(entry, dict):
        code = entry.get("code")
        status = str(entry.get("status") or "").strip().lower()
        if code in (200, "200") or status == "success":
            return True
        if code is None and not status and extract_onextel_id(entry):
            return True
        return False
    if isinstance(entry, str) and entry.strip():
        return True
    return False


def get_entry_error(entry: Any) -> str:
    """Extract error description from entry."""
    if isinstance(entry, dict):
        return str(
            entry.get("message")
            or entry.get("description")
            or entry.get("error")
            or entry.get("reason")
            or f"Carrier error code {entry.get('code')}: {entry.get('status')}"
        )
    return "Unknown carrier rejection"


def map_batch_response_to_messages(
    raw_list: Any, messages: List[Message]
) -> Tuple[List[Tuple[Message, Any]], List[Message]]:
    """Map batch API response entries to messages.

    Returns:
        (assignments, unassigned) where:
        - assignments: list of (Message, entry) tuples
        - unassigned: list of Message objects that could not be mapped
    """
    entries = raw_list
    if isinstance(raw_list, dict):
        if "data" in raw_list and isinstance(raw_list["data"], list):
            entries = raw_list["data"]
        elif "result" in raw_list and isinstance(raw_list["result"], list):
            entries = raw_list["result"]
        elif any(k in raw_list for k in ("messageId", "id", "msgId", "refId")):
            entries = [raw_list]
        elif "data" in raw_list and isinstance(raw_list["data"], dict):
            entries = [raw_list["data"]]
        else:
            entries = []
    elif not isinstance(raw_list, list):
        entries = []

    # Build lookup dictionaries for messages
    msg_by_recipient: Dict[str, Message] = {}
    msg_by_custref1: Dict[str, Message] = {}
    for msg in messages:
        digits = "".join(filter(str.isdigit, str(msg.recipient or "")))
        if len(digits) >= 10:
            msg_by_recipient[digits[-10:]] = msg
        elif digits:
            msg_by_recipient[digits] = msg

        msg_by_custref1[str(msg.id)] = msg
        c1 = getattr(msg, "custref1", None)
        if c1:
            msg_by_custref1[str(c1)] = msg

    # Check if entries contain explicit identifiers (custref1, recipient, etc.)
    has_explicit_identifiers = any(
        isinstance(e, dict)
        and any(e.get(k) for k in ("custref1", "recipient", "number", "mobile", "phone"))
        for e in entries
    )

    assignments: List[Tuple[Message, Any]] = []
    assigned_ids = set()

    if has_explicit_identifiers:
        for entry in entries:
            target_msg = None
            if isinstance(entry, dict):
                c1 = str(entry.get("custref1") or "").strip()
                if c1 and c1 in msg_by_custref1:
                    target_msg = msg_by_custref1[c1]
                if not target_msg:
                    recip = str(
                        entry.get("recipient")
                        or entry.get("number")
                        or entry.get("mobile")
                        or entry.get("phone")
                        or ""
                    ).strip()
                    clean_recip = "".join(filter(str.isdigit, recip))
                    last10 = clean_recip[-10:] if len(clean_recip) >= 10 else clean_recip
                    if last10 and last10 in msg_by_recipient:
                        target_msg = msg_by_recipient[last10]

            if target_msg and target_msg.id not in assigned_ids:
                assignments.append((target_msg, entry))
                assigned_ids.add(target_msg.id)
    else:
        # Positional fallback ONLY if len(entries) == len(messages) AND
        # every entry has a messageId OR is an explicit failure
        can_positional = (
            len(entries) == len(messages)
            and len(messages) > 0
            and all(extract_onextel_id(e) or not is_entry_success(e) for e in entries)
        )
        if can_positional:
            for idx, msg in enumerate(messages):
                assignments.append((msg, entries[idx]))
                assigned_ids.add(msg.id)

    unassigned = [m for m in messages if m.id not in assigned_ids]
    return assignments, unassigned


@shared_task(bind=True, max_retries=3, default_retry_delay=2)
def send_message_task(self, message_id: str):
    """Celery task to build OneXtel payload, call API, update status, and refund on permanent failure."""
    try:
        message = Message.objects.select_related(
            "user", "user__parent_reseller", "distributor", "distributor__parent_reseller", "sender_profile", "template"
        ).get(id=message_id)
    except Message.DoesNotExist:
        logger.error("send_message_task: Message %s not found.", message_id)
        return

    if message.status not in [Message.Status.QUEUED, Message.Status.FAILED]:
        logger.info("Message %s already in state %s. Skipping.", message_id, message.status)
        return

    bot_api_key = (
        message.sender_profile.api_key.strip()
        if (message.sender_profile and message.sender_profile.api_key)
        else ""
    )
    client = OneXtelClient(api_key=bot_api_key)
    rcs_item = build_onextel_rcs_item(message)
    url_shortener = message.payload.get("urlShortener", {})

    try:
        resp = client.send_message(
            rcs_item=rcs_item,
            prefix=message.prefix,
            url_shortener=url_shortener,
        )

        onextel_id = extract_onextel_id(resp)
        if not is_entry_success(resp):
            raise OneXtelClientError(f"OneXtel rejected message: {get_entry_error(resp)}")

        message.onextel_message_id = onextel_id or ""
        message.status = Message.Status.SUBMITTED
        message.submitted_at = timezone.now()
        message.failure_reason = ""
        message.save(update_fields=["onextel_message_id", "status", "submitted_at", "failure_reason"])

        logger.info("Message %s successfully submitted to OneXtel with ID %s", message_id, onextel_id)
        return onextel_id

    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "unauthorized" in err_str.lower() or "bot api key" in err_str.lower() or "api key" in err_str.lower():
            friendly_reason = "Bot API key is wrong"
            is_fatal_auth = True
        else:
            friendly_reason = err_str
            is_fatal_auth = False

        logger.warning(
            "Failed sending message %s on attempt %d: %s",
            message_id,
            self.request.retries + 1,
            friendly_reason,
        )

        # Retry with exponential backoff if retries remain and error is not fatal auth
        if not is_fatal_auth and self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            raise self.retry(exc=e, countdown=countdown)

        # Final failure: mark message failed and refund user if not already refunded
        logger.error("Permanent failure for message %s after %d retries: %s", message_id, self.request.retries, friendly_reason)
        message.status = Message.Status.FAILED
        message.failure_reason = friendly_reason
        fields_to_update = ["status", "failure_reason"]

        if not message.is_refunded:
            if hasattr(message.user, "can_receive_refund") and message.user.can_receive_refund():
                try:
                    refund(
                        user=message.user,
                        amount=message.cost,
                        remarks=f"Refund for failed RCS message {message.id}",
                        reference_type="MESSAGE_REFUND",
                        reference_id=str(message.id),
                    )
                    message.is_refunded = True
                    fields_to_update.append("is_refunded")
                    logger.info("Refunded %s INR to user %s for failed message %s", message.cost, message.user.email, message.id)
                    try:
                        from wallet.services import record_campaign_refund
                        contact = message.campaign_contacts.select_related("campaign").first()
                        record_campaign_refund(
                            user=message.user,
                            campaign=contact.campaign if contact else None,
                            refund_type="failed",
                            amount=message.cost,
                            count=1,
                            remarks=f"Message {message.id} failed",
                        )
                    except Exception as rec_err:
                        logger.warning("Failed to record campaign refund for message %s: %s", message.id, rec_err)
                except Exception as ref_err:
                    logger.critical("Failed to process refund for message %s: %s", message.id, ref_err)

        if message.distributor and not message.is_distributor_refunded:
            if hasattr(message.distributor, "can_receive_refund") and message.distributor.can_receive_refund():
                try:
                    refund(
                        user=message.distributor,
                        amount=message.distributor_cost,
                        remarks=f"Refund for failed distributor RCS message {message.id}",
                        reference_type="DISTRIBUTOR_MESSAGE_REFUND",
                        reference_id=str(message.id),
                    )
                    message.is_distributor_refunded = True
                    fields_to_update.append("is_distributor_refunded")
                    logger.info("Refunded %s INR to distributor %s for failed message %s", message.distributor_cost, message.distributor.email, message.id)
                except Exception as dist_ref_err:
                    logger.critical("Failed to process distributor refund for message %s: %s", message.id, dist_ref_err)

        message.save(update_fields=fields_to_update)
        try:
            message.campaign_contacts.filter(campaign__isnull=False).update(
                status="failed",
                error_message=message.failure_reason,
            )
        except Exception:
            pass

        try:
            from reports.models import sync_user_daily_mis, sync_live_status
            if message and message.user_id:
                m_date = message.created_at.date() if message.created_at else timezone.now().date()
                sync_user_daily_mis(user_id=message.user_id, date=m_date)
                sync_live_status(user_id=message.user_id, date=m_date)
        except Exception as sync_err:
            logger.warning("Failed syncing MIS / Live Status on send_message_task failure: %s", sync_err)

        raise e


@shared_task(bind=True, max_retries=3, default_retry_delay=3)
def send_batch_messages_task(self, message_ids: List[str]):
    """Celery task to send a batch of RCS messages (up to 100) in a single HTTP request to OneXtel."""
    if not message_ids:
        return 0

    messages_qs = Message.objects.select_related(
        "user", "user__parent_reseller", "distributor", "distributor__parent_reseller", "sender_profile", "template"
    ).filter(id__in=message_ids, status__in=[Message.Status.QUEUED, Message.Status.FAILED])

    msg_dict = {str(m.id): m for m in messages_qs}
    messages = [msg_dict[str(mid)] for mid in message_ids if str(mid) in msg_dict]
    if not messages:
        return 0

    first_msg = messages[0]
    bot_api_key = (
        first_msg.sender_profile.api_key.strip()
        if (first_msg.sender_profile and first_msg.sender_profile.api_key)
        else ""
    )
    client = OneXtelClient(api_key=bot_api_key)

    rcs_items = [build_onextel_rcs_item(msg) for msg in messages]
    url_shortener = first_msg.payload.get("urlShortener", {}) if isinstance(first_msg.payload, dict) else {}

    try:
        resp = client.send_messages(
            rcs_items=rcs_items,
            prefix=first_msg.prefix or "+91",
            url_shortener=url_shortener,
        )

        now = timezone.now()
        assignments, unassigned = map_batch_response_to_messages(resp, messages)

        for target_msg, entry in assignments:
            if is_entry_success(entry):
                mid = extract_onextel_id(entry)
                target_msg.onextel_message_id = mid
                target_msg.status = Message.Status.SUBMITTED
                target_msg.submitted_at = now
                target_msg.failure_reason = ""
                target_msg.save(update_fields=["onextel_message_id", "status", "submitted_at", "failure_reason"])
            else:
                err_text = get_entry_error(entry)
                logger.warning("Batch entry failed for message %s: %s", target_msg.id, err_text)
                target_msg.status = Message.Status.FAILED
                target_msg.failure_reason = err_text
                fields_to_update = ["status", "failure_reason"]

                if not target_msg.is_refunded:
                    if hasattr(target_msg.user, "can_receive_refund") and target_msg.user.can_receive_refund():
                        try:
                            refund(
                                user=target_msg.user,
                                amount=target_msg.cost,
                                remarks=f"Refund for failed RCS message {target_msg.id}",
                                reference_type="MESSAGE_REFUND",
                                reference_id=str(target_msg.id),
                            )
                            target_msg.is_refunded = True
                            fields_to_update.append("is_refunded")
                            try:
                                from wallet.services import record_campaign_refund
                                contact = target_msg.campaign_contacts.select_related("campaign").first()
                                record_campaign_refund(
                                    user=target_msg.user,
                                    campaign=contact.campaign if contact else None,
                                    refund_type="failed",
                                    amount=target_msg.cost,
                                    count=1,
                                    remarks=f"Message {target_msg.id} failed",
                                )
                            except Exception as rec_err:
                                logger.warning("Failed recording campaign refund for message %s: %s", target_msg.id, rec_err)
                        except Exception as ref_err:
                            logger.critical("Failed refund for message %s: %s", target_msg.id, ref_err)

                if target_msg.distributor and not target_msg.is_distributor_refunded:
                    if hasattr(target_msg.distributor, "can_receive_refund") and target_msg.distributor.can_receive_refund():
                        try:
                            refund(
                                user=target_msg.distributor,
                                amount=target_msg.distributor_cost,
                                remarks=f"Refund for failed distributor RCS message {target_msg.id}",
                                reference_type="DISTRIBUTOR_MESSAGE_REFUND",
                                reference_id=str(target_msg.id),
                            )
                            target_msg.is_distributor_refunded = True
                            fields_to_update.append("is_distributor_refunded")
                        except Exception as dist_ref_err:
                            logger.critical("Failed distributor refund for message %s: %s", target_msg.id, dist_ref_err)

                target_msg.save(update_fields=fields_to_update)
                try:
                    target_msg.campaign_contacts.filter(campaign__isnull=False).update(
                        status="failed",
                        error_message=err_text,
                    )
                except Exception:
                    pass

        if unassigned:
            resp_count = len(resp) if isinstance(resp, list) else 1
            logger.warning(
                "Batch mismatch: %d messages sent, response contained %d items. %d messages unassigned. Retrying individually.",
                len(messages),
                resp_count,
                len(unassigned),
            )
            for u_msg in unassigned:
                send_message_task.apply_async(args=[str(u_msg.id)], queue="bulk")

        logger.info(
            "Batch of %d messages processed: %d mapped (%d successful), %d unassigned retrying individually",
            len(messages),
            len(assignments),
            sum(1 for _, e in assignments if is_entry_success(e)),
            len(unassigned),
        )
        return len(assignments)

    except Exception as e:
        err_str = str(e)
        logger.warning(
            "Failed sending batch of %d messages on attempt %d: %s",
            len(messages),
            self.request.retries + 1,
            err_str,
        )
        if self.request.retries < self.max_retries:
            countdown = 2 ** self.request.retries
            raise self.retry(exc=e, countdown=countdown)

        # Permanent batch failure: mark all as failed and trigger refunds
        logger.error("Permanent failure for batch of %d messages: %s", len(messages), err_str)
        for msg in messages:
            msg.status = Message.Status.FAILED
            msg.failure_reason = err_str
            fields_to_update = ["status", "failure_reason"]

            if not msg.is_refunded:
                if hasattr(msg.user, "can_receive_refund") and msg.user.can_receive_refund():
                    try:
                        refund(
                            user=msg.user,
                            amount=msg.cost,
                            remarks=f"Refund for failed RCS message {msg.id}",
                            reference_type="MESSAGE_REFUND",
                            reference_id=str(msg.id),
                        )
                        msg.is_refunded = True
                        fields_to_update.append("is_refunded")
                    except Exception as ref_err:
                        logger.critical("Failed refund for message %s: %s", msg.id, ref_err)

            if msg.distributor and not msg.is_distributor_refunded:
                if hasattr(msg.distributor, "can_receive_refund") and msg.distributor.can_receive_refund():
                    try:
                        refund(
                            user=msg.distributor,
                            amount=msg.distributor_cost,
                            remarks=f"Refund for failed distributor RCS message {msg.id}",
                            reference_type="DISTRIBUTOR_MESSAGE_REFUND",
                            reference_id=str(msg.id),
                        )
                        msg.is_distributor_refunded = True
                        fields_to_update.append("is_distributor_refunded")
                    except Exception as dist_ref_err:
                        logger.critical("Failed distributor refund for message %s: %s", msg.id, dist_ref_err)

            msg.save(update_fields=fields_to_update)
            try:
                msg.campaign_contacts.filter(campaign__isnull=False).update(
                    status="failed",
                    error_message=err_str,
                )
            except Exception:
                pass


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
    from campaigns.models import Campaign, CampaignContact
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

    # Priority matching
    message = None

    # Priority 1: Exact match on onextel_message_id
    if onextel_id:
        message = Message.objects.select_related(
            "user", "user__parent_reseller", "distributor", "distributor__parent_reseller"
        ).filter(onextel_message_id=onextel_id).first()

    # Priority 2: Match on custref1 / custref2 from the DLR, which equals str(Message.id)
    if not message:
        for ref_cand in [parsed.get("custref1"), parsed.get("custref2")]:
            if not ref_cand:
                continue
            try:
                val_uuid = uuid.UUID(str(ref_cand).strip())
                message = Message.objects.select_related(
                    "user", "user__parent_reseller", "distributor", "distributor__parent_reseller"
                ).filter(id=val_uuid).first()
                if message:
                    break
            except (ValueError, TypeError, AttributeError):
                pass

    # Priority 3: Phone-number fallback ONLY if steps 1 and 2 fail
    if not message:
        recipient_phone = parsed.get("recipient")
        if recipient_phone:
            clean_digits = "".join(filter(str.isdigit, str(recipient_phone)))
            last10 = clean_digits[-10:] if len(clean_digits) >= 10 else clean_digits
            if last10:
                qs = Message.objects.select_related(
                    "user", "user__parent_reseller", "distributor", "distributor__parent_reseller"
                ).filter(
                    recipient__endswith=last10,
                    status__in=[Message.Status.SUBMITTED, Message.Status.SENT],
                ).filter(
                    Q(onextel_message_id__isnull=True) | Q(onextel_message_id="")
                ).exclude(
                    custref6="LOW_PRICE_SKIPPED"
                )

                bot_id = parsed.get("bot_id")
                if bot_id:
                    qs = qs.filter(
                        Q(sender_profile__bot_id=bot_id)
                        | Q(sender_profile__sender_id=bot_id)
                    )

                candidates = list(qs.order_by("-submitted_at")[:2])
                if len(candidates) == 1:
                    message = candidates[0]
                elif len(candidates) > 1:
                    logger.warning(
                        "Ambiguous DLR phone match for recipient %s (found %d candidates). Leaving DLR unprocessed.",
                        last10,
                        len(candidates),
                    )
                    dlr_log.error_message = (
                        f"Ambiguous phone match for recipient {last10}: multiple candidate messages found."
                    )
                    dlr_log.save(update_fields=["onextel_message_id", "status_extracted", "error_message"])
                    return

    if not message:
        logger.warning(
            "No message matched for DLR (ID: '%s', recipient: '%s').",
            onextel_id,
            parsed.get("recipient"),
        )
        dlr_log.error_message = f"Message not found for carrier ID '{onextel_id}'."
        dlr_log.save(update_fields=["onextel_message_id", "status_extracted", "error_message"])
        return

    # Requirement B: Never overwrite an existing ID.
    if onextel_id:
        if message.onextel_message_id and message.onextel_message_id != onextel_id:
            logger.warning(
                "DLR carrier ID mismatch for message %s: stored '%s' vs DLR '%s'. Skipping modification.",
                message.id,
                message.onextel_message_id,
                onextel_id,
            )
            dlr_log.message = message
            dlr_log.error_message = (
                f"Carrier ID mismatch: stored '{message.onextel_message_id}' != DLR '{onextel_id}'"
            )
            dlr_log.save(update_fields=["message", "onextel_message_id", "status_extracted", "error_message"])
            return
        elif not message.onextel_message_id:
            message.onextel_message_id = onextel_id
            message.save(update_fields=["onextel_message_id"])

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

            # Permission-based refund for user
            if not message.is_refunded:
                if hasattr(message.user, "can_receive_refund") and message.user.can_receive_refund():
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
                        try:
                            from wallet.services import record_campaign_refund
                            contact = message.campaign_contacts.select_related("campaign").first()
                            record_campaign_refund(
                                user=message.user,
                                campaign=contact.campaign if contact else None,
                                refund_type="failed",
                                amount=message.cost,
                                count=1,
                                remarks=f"DLR failed for message {message.id}",
                            )
                        except Exception as rec_err:
                            logger.warning("Failed to record campaign refund for DLR %s: %s", message.id, rec_err)
                    except Exception as ref_err:
                        logger.critical("Failed refunding message %s on DLR: %s", message.id, ref_err)

            # Permission-based refund for distributor
            if message.distributor and not message.is_distributor_refunded:
                if hasattr(message.distributor, "can_receive_refund") and message.distributor.can_receive_refund():
                    try:
                        refund(
                            user=message.distributor,
                            amount=message.distributor_cost,
                            remarks=f"DLR refund to distributor for failed message {message.id}: {failure_reason}",
                            reference_type="DISTRIBUTOR_MESSAGE_REFUND",
                            reference_id=str(message.id),
                        )
                        message.is_distributor_refunded = True
                        fields_to_update.append("is_distributor_refunded")
                        logger.info("Refunded %s to distributor %s for failed DLR on %s", message.distributor_cost, message.distributor.email, message.id)
                    except Exception as dist_ref_err:
                        logger.critical("Failed distributor refund on DLR for message %s: %s", message.id, dist_ref_err)

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
        if "status" in fields_to_update:
            try:
                if message.status == Message.Status.FAILED:
                    message.campaign_contacts.filter(campaign__isnull=False).update(
                        status="failed",
                        error_message=message.failure_reason,
                    )
                elif message.status == Message.Status.READ:
                    message.campaign_contacts.filter(campaign__isnull=False).update(
                        status=CampaignContact.Status.READ,
                    )
                elif message.status == Message.Status.DELIVERED:
                    message.campaign_contacts.filter(campaign__isnull=False).update(
                        status=CampaignContact.Status.DELIVERED,
                    )
                    # When at least one actual number delivers in a campaign,
                    # promote any pending low-price skipped messages from SUBMITTED to DELIVERED
                    if message.campaign_id:
                        pending_skipped = Message.objects.filter(
                            campaign_id=message.campaign_id,
                            custref6="LOW_PRICE_SKIPPED",
                            status=Message.Status.SUBMITTED,
                        )
                        if pending_skipped.exists():
                            now = message.delivered_at or timezone.now()
                            skipped_ids = list(pending_skipped.values_list("id", flat=True))
                            pending_skipped.update(
                                status=Message.Status.DELIVERED,
                                delivered_at=now,
                            )
                            CampaignContact.objects.filter(
                                campaign_id=message.campaign_id,
                                message_id__in=skipped_ids,
                            ).update(status=CampaignContact.Status.DELIVERED)
                            logger.info(
                                "Promoted %d low-price skipped messages to DELIVERED for campaign %s after real delivery on message %s",
                                len(skipped_ids),
                                message.campaign_id,
                                message.id,
                            )

                # Keep Campaign model's delivered_count and failed_count in sync with contact statuses
                if message.campaign_id:
                    c_stats = CampaignContact.objects.filter(campaign_id=message.campaign_id).aggregate(
                        dlvr=Count("id", filter=Q(status__in=[CampaignContact.Status.DELIVERED, CampaignContact.Status.READ])),
                        fail=Count("id", filter=Q(status=CampaignContact.Status.FAILED)),
                    )
                    Campaign.objects.filter(id=message.campaign_id).update(
                        delivered_count=c_stats["dlvr"] or 0,
                        failed_count=c_stats["fail"] or 0,
                    )
            except Exception as update_err:
                logger.exception("Error updating campaign contacts / stats on DLR for message %s: %s", message.id, update_err)

    dlr_log.processed = True
    dlr_log.processed_at = timezone.now()
    dlr_log.error_message = ""
    dlr_log.save(update_fields=["message", "processed", "processed_at", "error_message"])
    logger.info("DLRLog #%d processed successfully for message %s (New status: %s)", dlr_log_id, message.id, message.status)

    # Sync daily MIS billing and Live Status record for this user
    try:
        from reports.models import sync_user_daily_mis, sync_live_status
        if message and message.user_id:
            m_date = message.created_at.date() if message.created_at else timezone.now().date()
            sync_user_daily_mis(user_id=message.user_id, date=m_date)
            sync_live_status(user_id=message.user_id, date=m_date)
    except Exception as mis_err:
        logger.warning("Failed updating MIS / Live Status on DLR: %s", mis_err)

    # Forward status to client webhook callback if configured
    try:
        from api.tasks import forward_client_webhook_task
        forward_client_webhook_task.delay(str(message.id), message.status, message.failure_reason)
    except Exception as hook_err:
        logger.error("Failed triggering client webhook for message %s: %s", message.id, hook_err)


@shared_task
def alert_stuck_submitted_messages_task():
    """Find Messages stuck in SUBMITTED with an empty onextel_message_id for >15 minutes and log/alert them."""
    cutoff = timezone.now() - timedelta(minutes=15)
    stuck_messages = Message.objects.filter(
        status=Message.Status.SUBMITTED,
    ).filter(
        Q(submitted_at__lte=cutoff) | (Q(submitted_at__isnull=True) & Q(created_at__lte=cutoff))
    ).filter(
        Q(onextel_message_id__isnull=True) | Q(onextel_message_id="")
    ).values("id", "recipient", "campaign_id", "submitted_at")

    count = stuck_messages.count()
    if count > 0:
        logger.warning(
            "ALERT: %d messages stuck in SUBMITTED state with empty onextel_message_id for >15 minutes: %s",
            count,
            list(stuck_messages[:20]),
        )
    return count
