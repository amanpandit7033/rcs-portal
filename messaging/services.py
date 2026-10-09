"""Business services for message submission, balance debit, and queue dispatch."""
from decimal import Decimal
import json
import re
from typing import Any, Dict, Optional
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from templates_mgmt.models import Template
from wallet.models import RatePlan, SenderProfile
from wallet.services import debit, InsufficientBalanceError
from .models import Message


INDIAN_PHONE_REGEX = re.compile(r"^[6-9]\d{9}$")


def normalize_and_validate_indian_phone(phone_input: str) -> str:
    """Normalize and validate a 10-digit Indian phone number."""
    if not phone_input:
        raise ValidationError("Recipient phone number is required.")

    # Remove all whitespace, dashes, brackets
    digits = re.sub(r"[^\d]", "", str(phone_input).strip())

    # Handle standard country code prefixes
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10:
        raise ValidationError(f"Invalid phone number '{phone_input}'. Expected 10 digits, got {len(digits)}.")

    if not INDIAN_PHONE_REGEX.match(digits):
        raise ValidationError(
            f"Phone number '{digits}' must be a valid 10-digit Indian mobile number starting with 6, 7, 8, or 9."
        )

    return digits


def get_user_rate_for_type(user, message_type: str) -> Decimal:
    """Find rate for user and message type, checking parent reseller hierarchy if needed."""
    plan = RatePlan.objects.filter(user=user, message_type=message_type).first()
    if plan:
        return plan.rate

    if getattr(user, "parent_reseller", None):
        plan = RatePlan.objects.filter(user=user.parent_reseller, message_type=message_type).first()
        if plan:
            return plan.rate

    # Default standard rates fallback if no specific plan is assigned
    defaults = {
        "PROMOTIONAL": Decimal("0.2500"),
        "TRANSACTIONAL": Decimal("0.2000"),
        "OTP": Decimal("0.1500"),
    }
    if message_type in defaults:
        return defaults[message_type]

    raise ValidationError(f"No active rate plan found for user {user.email} with message type '{message_type}'.")


def submit_message(
    user,
    sender_profile_id: int,
    recipient: str,
    message_type: str,
    content_type: str = Message.ContentType.TEXT,
    prefix: str = "+91",
    payload: Optional[Dict[str, Any]] = None,
    template_id: Optional[int] = None,
    custom_params: Optional[Dict[str, Any]] = None,
    custref1: str = "",
    custref2: str = "",
    custref3: str = "",
    custref4: str = "",
    custref5: str = "",
    custref6: str = "",
    ttl: Optional[int] = None,
    url_shortener_flag: bool = False,
    url_shortener_track_user: bool = False,
) -> Message:
    """Validate, debit wallet, persist queued Message in ONE atomic transaction, and queue Celery task."""
    from .tasks import send_message_task

    # 1. Validate phone number
    clean_recipient = normalize_and_validate_indian_phone(recipient)

    # 2. Validate Sender Profile
    if getattr(user, "is_admin", False) or getattr(user, "is_superuser", False):
        sender_profile = SenderProfile.objects.filter(id=sender_profile_id, is_active=True).first()
    else:
        sender_profile = SenderProfile.objects.filter(id=sender_profile_id, user=user, is_active=True).first()

    if not sender_profile:
        raise ValidationError("Active Sender Profile not found or not assigned to user.")

    # 3. Validate Content / Template
    template = None
    payload_data = dict(payload or {})

    if content_type == Message.ContentType.TEMPLATE:
        if not template_id:
            raise ValidationError("A template must be selected when content_type is 'template'.")

        template = Template.objects.for_user(user).filter(id=template_id).first()
        if not template:
            raise ValidationError(f"Template with ID {template_id} not found.")

        # STRICT GATE: Template can only be used for sending when approved
        if not template.is_approved:
            raise ValidationError(
                f"Template '{template.name}' cannot be used for sending. "
                f"Current status is '{template.get_status_display()}', must be 'Approved'."
            )

        # Sync message_type if not explicitly set
        if not message_type:
            message_type = template.message_type

    elif content_type == Message.ContentType.TEXT:
        text = str(payload_data.get("text", "")).strip()
        if not text:
            raise ValidationError("Plain text message cannot be empty.")

    elif content_type in [Message.ContentType.RICH_CARD, Message.ContentType.CAROUSEL]:
        if not payload_data:
            raise ValidationError(f"Content payload for {content_type} cannot be empty.")

    # 4. Fetch Rate & Calculate Cost
    rate_val = get_user_rate_for_type(user, message_type)
    cost = rate_val  # 1 message

    # Store URL shortener options in payload for sender task
    payload_data["urlShortener"] = {
        "flag": bool(url_shortener_flag),
        "track_user": bool(url_shortener_track_user),
    }

    # 5. Atomic Transaction: Debit Wallet + Persist Queued Message
    msg_id = uuid.uuid4()

    with transaction.atomic():
        # Debit wallet: raises InsufficientBalanceError if insufficient funds
        debit(
            user=user,
            amount=cost,
            remarks=f"RCS {message_type} message to {clean_recipient}",
            reference_type="MESSAGE",
            reference_id=str(msg_id),
        )

        message = Message.objects.create(
            id=msg_id,
            user=user,
            sender_profile=sender_profile,
            message_type=message_type,
            recipient=clean_recipient,
            prefix=prefix or "+91",
            content_type=content_type,
            payload=payload_data,
            template=template,
            custom_params=custom_params or {},
            custref1=custref1[:100],
            custref2=custref2[:100],
            custref3=custref3[:100],
            custref4=custref4[:100],
            custref5=custref5[:100],
            custref6=custref6[:100],
            ttl=ttl,
            rate_applied=rate_val,
            cost=cost,
            status=Message.Status.QUEUED,
        )

    # Queue routing: priority for OTP/Transactional, bulk for Promotional
    if message_type in [Message.MessageType.OTP, Message.MessageType.TRANSACTIONAL]:
        queue_name = "priority"
    else:
        queue_name = "bulk"

    # Dispatch Celery task now that atomic transaction is committed
    send_message_task.apply_async(args=[str(message.id)], queue=queue_name)

    return message
