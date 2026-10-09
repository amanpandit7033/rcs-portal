"""Messaging models for RCS message dispatch, tracking, and logs."""
from decimal import Decimal
import uuid
from django.conf import settings
from django.db import models
from accounts.managers import RoleScopedQuerySetMixin


class MessageQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    user_field = "user"


class MessageManager(models.Manager.from_queryset(MessageQuerySet)):
    def for_user(self, user):
        return self.get_queryset().for_user(user)


class Message(models.Model):
    """RCS Message record tracking single message dispatch via OneXtel."""

    class MessageType(models.TextChoices):
        PROMOTIONAL = "PROMOTIONAL", "Promotional"
        TRANSACTIONAL = "TRANSACTIONAL", "Transactional"
        OTP = "OTP", "OTP"

    class ContentType(models.TextChoices):
        TEXT = "text", "Plain Text"
        RICH_CARD = "rich_card", "Rich Card"
        CAROUSEL = "carousel", "Carousel"
        TEMPLATE = "template", "Pre-approved Template"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SUBMITTED = "submitted", "Submitted"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rcs_messages",
    )
    sender_profile = models.ForeignKey(
        "wallet.SenderProfile",
        on_delete=models.PROTECT,
        related_name="rcs_messages",
    )
    message_type = models.CharField(
        "Message Type",
        max_length=20,
        choices=MessageType.choices,
        db_index=True,
    )
    recipient = models.CharField("Recipient Phone", max_length=20)
    prefix = models.CharField("Country Prefix", max_length=10, default="+91")
    content_type = models.CharField(
        "Content Type",
        max_length=20,
        choices=ContentType.choices,
        default=ContentType.TEXT,
    )
    payload = models.JSONField(
        "Content Payload",
        default=dict,
        blank=True,
        help_text="Custom content payload (text, rich card, or carousel)",
    )
    template = models.ForeignKey(
        "templates_mgmt.Template",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
    )
    custom_params = models.JSONField(
        "Template Custom Params",
        default=dict,
        blank=True,
        help_text="Key-value substitution variables for approved template placeholders",
    )

    # Custom tracking references
    custref1 = models.CharField("Custom Ref 1", max_length=100, blank=True, default="")
    custref2 = models.CharField("Custom Ref 2", max_length=100, blank=True, default="")
    custref3 = models.CharField("Custom Ref 3", max_length=100, blank=True, default="")
    custref4 = models.CharField("Custom Ref 4", max_length=100, blank=True, default="")
    custref5 = models.CharField("Custom Ref 5", max_length=100, blank=True, default="")
    custref6 = models.CharField("Custom Ref 6", max_length=100, blank=True, default="")

    ttl = models.PositiveIntegerField(
        "Time To Live (Seconds)",
        null=True,
        blank=True,
        help_text="TTL duration in seconds",
    )

    # Cost accounting snapshot
    rate_applied = models.DecimalField(
        "Rate Applied (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Frozen unit rate applied at the moment of queuing",
    )
    cost = models.DecimalField(
        "Total Cost (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Frozen total cost debited from wallet",
    )

    # Lifecycle and status tracking
    status = models.CharField(
        "Delivery Status",
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    onextel_message_id = models.CharField(
        "OneXtel Message ID",
        max_length=100,
        null=True,
        blank=True,
        db_index=True,
    )
    failure_reason = models.TextField("Failure Reason", blank=True, default="")
    is_refunded = models.BooleanField("Is Refunded", default=False, db_index=True)
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    submitted_at = models.DateTimeField("Submitted to Carrier At", null=True, blank=True)
    delivered_at = models.DateTimeField("Delivered At", null=True, blank=True)
    read_at = models.DateTimeField("Read At", null=True, blank=True)

    objects = MessageManager()

    class Meta:
        verbose_name = "RCS Message"
        verbose_name_plural = "RCS Messages"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["onextel_message_id"]),
        ]

    def __str__(self):
        return f"{self.id} -> {self.recipient} ({self.status})"


class DLRLog(models.Model):
    """Raw Delivery Report (DLR) webhook records from OneXtel."""

    id = models.BigAutoField(primary_key=True)
    payload = models.JSONField("Raw Webhook Payload", default=dict)
    onextel_message_id = models.CharField(
        "OneXtel Message ID",
        max_length=100,
        blank=True,
        db_index=True,
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dlr_logs",
    )
    status_extracted = models.CharField("Extracted Status", max_length=50, blank=True)
    processed = models.BooleanField("Processed", default=False, db_index=True)
    error_message = models.TextField("Processing Error", blank=True, default="")
    source_ip = models.GenericIPAddressField("Source IP", null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "DLR Webhook Log"
        verbose_name_plural = "DLR Webhook Logs"
        ordering = ["-received_at"]

    def __str__(self):
        return f"DLR #{self.id} - {self.onextel_message_id or 'unknown'} ({'Processed' if self.processed else 'Pending'})"
