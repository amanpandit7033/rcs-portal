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

    # 1. Extract OneXtel Message ID
    message_id = (
        data.get("messageId")
        or data.get("message_id")
        or data.get("msgId")
        or data.get("id")
        or data.get("refId")
        or data.get("onextel_message_id")
        or ""
    )
    message_id = str(message_id).strip()

    # 2. Extract Raw Status String
    raw_status = (
        data.get("status")
        or data.get("deliveryStatus")
        or data.get("event")
        or data.get("state")
        or data.get("report")
        or ""
    )
    raw_status = str(raw_status).strip()
    status_upper = raw_status.upper()

    # 3. Map to Normalized Message.Status
    normalized_status: Optional[str] = None
    if any(k in status_upper for k in ["DELIVERED", "DELIVRD", "SUCCESS", "ARRIVED"]):
        normalized_status = Message.Status.DELIVERED
    elif any(k in status_upper for k in ["READ", "SEEN", "OPENED"]):
        normalized_status = Message.Status.READ
    elif any(k in status_upper for k in ["FAIL", "REJECT", "UNDELIV", "EXPIRED", "DND", "BLOCKED", "ERROR"]):
        normalized_status = Message.Status.FAILED
    elif any(k in status_upper for k in ["SENT", "DISPATCHED", "SUBMITTED", "TRANSMITTED"]):
        normalized_status = Message.Status.SENT

    # 4. Extract Failure Reason
    failure_reason = ""
    if normalized_status == Message.Status.FAILED or "error" in data or "reason" in data:
        failure_reason = str(
            data.get("reason")
            or data.get("description")
            or data.get("errorMessage")
            or data.get("cause")
            or data.get("errorCode")
            or raw_status
        ).strip()

    # 5. Extract Event Timestamp
    raw_ts = (
        data.get("timestamp")
        or data.get("eventTime")
        or data.get("deliveredAt")
        or data.get("updatedAt")
    )
    event_timestamp = None
    if raw_ts:
        if isinstance(raw_ts, (int, float)):
            # Epoch milliseconds or seconds
            try:
                epoch = raw_ts / 1000.0 if raw_ts > 1e11 else float(raw_ts)
                event_timestamp = datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone())
            except Exception:
                event_timestamp = timezone.now()
        elif isinstance(raw_ts, str):
            event_timestamp = parse_datetime(raw_ts) or timezone.now()
    else:
        event_timestamp = timezone.now()

    recipient = str(data.get("recipient") or data.get("mobile") or data.get("phone") or "").strip()

    return {
        "onextel_message_id": message_id,
        "status": normalized_status,
        "raw_status": raw_status,
        "failure_reason": failure_reason,
        "event_timestamp": event_timestamp or timezone.now(),
        "recipient": recipient,
        "raw": data,
    }
