"""OneXtel Delivery Report (DLR) Webhook Parser.

Centralized parsing logic for processing carrier delivery receipts.
Because vendor specifications may vary across carrier integrations, all payload extraction
is encapsulated within `parse_dlr_payload` with clear TODO annotations.
"""
from datetime import datetime
import logging
from typing import Any, Dict, Optional
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Message

logger = logging.getLogger(__name__)


def parse_dlr_payload(payload: Any) -> Dict[str, Any]:
    """Parse raw DLR payload received from OneXtel webhook into normalized dict.

    Returns:
        Dict with keys:
            - onextel_message_id (str): Carrier message ID to match Message model.
            - status (str): Normalized Message.Status choice (delivered/read/failed/sent).
            - failure_reason (str): Cause of failure if failed.
            - event_timestamp (datetime): Timestamp when carrier reported event.
            - raw_status (str): Exact vendor status string.
            - recipient (str): Optional destination number if reported.
            - raw (dict): Original payload dict.
    """
    if isinstance(payload, list) and len(payload) > 0:
        data = payload[0]
    elif isinstance(payload, dict):
        # Handle nested wrappers like {"dlr": {...}} or {"data": {...}} or {"event": {...}}
        if "dlr" in payload and isinstance(payload["dlr"], dict):
            data = payload["dlr"]
        elif "data" in payload and isinstance(payload["data"], dict):
            data = payload["data"]
        else:
            data = payload
    else:
        data = {}

    # -------------------------------------------------------------------------
    # TODO (OneXtel Carrier Specs):
    # Verify exact field names once OneXtel production webhook documentation is confirmed.
    # Current mapping checks standard enterprise aggregator fields:
    #   Message ID: 'messageId', 'message_id', 'msgId', 'id', 'refId', 'onextel_message_id'
    #   Status: 'status', 'deliveryStatus', 'event', 'state', 'report'
    #   Failure Reason: 'reason', 'description', 'errorMessage', 'cause', 'errorCode'
    #   Timestamp: 'timestamp', 'eventTime', 'deliveredAt', 'updatedAt'
    # -------------------------------------------------------------------------

    # Extract nested 'entity' dictionary (Telspiel / OneXtel official schema)
    entity = data.get("entity") if isinstance(data.get("entity"), dict) else {}

    # 1. Extract OneXtel Message ID (check entity first, then root)
    message_id = (
        entity.get("messageId")
        or entity.get("message_id")
        or data.get("messageId")
        or data.get("message_id")
        or data.get("msgId")
        or data.get("id")
        or data.get("refId")
        or data.get("onextel_message_id")
        or ""
    )
    message_id = str(message_id).strip()

    # 2. Extract Raw Status String (check entity.eventType first, then root status/event)
    raw_status = (
        entity.get("eventType")
        or data.get("eventType")
        or data.get("status")
        or data.get("deliveryStatus")
        or data.get("event")
        or data.get("state")
        or data.get("report")
        or ""
    )
    raw_status = str(raw_status).strip()
    status_upper = raw_status.upper()

    # 3. Extract Error / Failure details (check entity.error first)
    failure_reason = ""
    entity_err = entity.get("error") if isinstance(entity.get("error"), dict) else {}
    if entity_err:
        err_msg = entity_err.get("message") or ""
        err_code = entity_err.get("errCode") or entity_err.get("code") or ""
        if err_msg or err_code:
            failure_reason = f"{err_code}: {err_msg}".strip(" :")

    # 4. Map to Normalized Message.Status
    normalized_status: Optional[str] = None
    if any(k in status_upper for k in ["DELIVERED", "DELIVRD", "SUCCESS", "ARRIVED"]):
        normalized_status = Message.Status.DELIVERED
    elif any(k in status_upper for k in ["READ", "SEEN", "OPENED"]):
        normalized_status = Message.Status.READ
    elif any(k in status_upper for k in ["FAIL", "REJECT", "UNDELIV", "EXPIRED", "DND", "BLOCKED", "ERROR"]):
        normalized_status = Message.Status.FAILED
    elif any(k in status_upper for k in ["SENT", "DISPATCHED", "SUBMITTED", "TRANSMITTED"]):
        normalized_status = Message.Status.SENT
    elif failure_reason:
        normalized_status = Message.Status.FAILED

    # Fallback failure reason if not from entity.error
    if not failure_reason and (normalized_status == Message.Status.FAILED or "error" in data or "reason" in data):
        failure_reason = str(
            data.get("reason")
            or data.get("description")
            or data.get("errorMessage")
            or data.get("cause")
            or data.get("errorCode")
            or raw_status
        ).strip()

    # 5. Extract Event Timestamp (check entity.sendTime first)
    raw_ts = (
        entity.get("sendTime")
        or data.get("sendTime")
        or data.get("timestamp")
        or data.get("eventTime")
        or data.get("deliveredAt")
        or data.get("updatedAt")
    )
    event_timestamp = None
    if raw_ts:
        if isinstance(raw_ts, (int, float)):
            try:
                epoch = raw_ts / 1000.0 if raw_ts > 1e11 else float(raw_ts)
                event_timestamp = datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone())
            except Exception:
                event_timestamp = timezone.now()
        elif isinstance(raw_ts, str):
            event_timestamp = parse_datetime(raw_ts)
            if not event_timestamp:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
                    try:
                        event_timestamp = timezone.make_aware(datetime.strptime(raw_ts.strip(), fmt))
                        break
                    except Exception:
                        pass
            if not event_timestamp:
                event_timestamp = timezone.now()
    else:
        event_timestamp = timezone.now()

    # 6. Extract Recipient Phone Number
    recipient = str(
        data.get("userPhoneNumber")
        or entity.get("senderPhoneNumber")
        or data.get("recipient")
        or data.get("mobile")
        or data.get("phone")
        or ""
    ).strip()

    return {
        "onextel_message_id": message_id,
        "status": normalized_status,
        "raw_status": raw_status,
        "failure_reason": failure_reason,
        "event_timestamp": event_timestamp or timezone.now(),
        "recipient": recipient,
        "custref1": str(data.get("custref1") or "").strip(),
        "custref2": str(data.get("custref2") or "").strip(),
        "custref3": str(data.get("custref3") or "").strip(),
        "bot_id": str(data.get("botId") or "").strip(),
        "raw": data,
    }
