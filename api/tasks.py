"""Celery task for forwarding DLR callbacks to client webhook endpoints with HMAC signing."""
import hashlib
import hmac
import json
import logging
import requests
from celery import shared_task
from django.utils import timezone

from .models import ClientWebhook, WebhookForwardLog

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def forward_client_webhook_task(self, message_id: str, new_status: str, failure_reason: str = ""):
    """Deliver delivery receipts to client callback URL signed with HMAC-SHA256."""
    from messaging.models import Message

    try:
        message = Message.objects.select_related("user").get(id=message_id)
    except Message.DoesNotExist:
        logger.warning("forward_client_webhook_task: Message %s not found.", message_id)
        return

    # Check if client configured an active webhook
    webhook = ClientWebhook.objects.filter(user=message.user, is_active=True).first()
    if not webhook or not webhook.url:
        return

    payload = {
        "event": "message.dlr",
        "id": str(message.id),  # OUR UUID
        "recipient": message.recipient,
        "status": new_status,
        "failure_reason": failure_reason or message.failure_reason,
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "timestamp": timezone.now().isoformat(),
    }
    payload_json = json.dumps(payload, separators=(",", ":"))

    # Generate HMAC SHA-256 signature
    signature = hmac.new(
        webhook.secret.encode("utf-8"),
        payload_json.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "RCS-Portal-Webhook/1.0",
        "X-Signature-SHA256": signature,
        "X-RCS-Signature": signature,
    }

    log_entry = WebhookForwardLog.objects.create(
        user=message.user,
        message_id=str(message.id),
        url=webhook.url,
        payload=payload,
        attempt=self.request.retries + 1,
    )

    try:
        resp = requests.post(webhook.url, data=payload_json, headers=headers, timeout=10)
        log_entry.status_code = resp.status_code
        log_entry.response_body = resp.text[:1000]
        log_entry.success = 200 <= resp.status_code < 300
        log_entry.save(update_fields=["status_code", "response_body", "success"])

        if not log_entry.success:
            if self.request.retries < self.max_retries:
                countdown = (2 ** self.request.retries) * 5
                raise self.retry(exc=Exception(f"Webhook HTTP {resp.status_code}"), countdown=countdown)
            return False

        return True

    except Exception as exc:
        log_entry.error_message = str(exc)
        log_entry.save(update_fields=["error_message"])
        if self.request.retries < self.max_retries:
            countdown = (2 ** self.request.retries) * 5
            raise self.retry(exc=exc, countdown=countdown)
        return False
