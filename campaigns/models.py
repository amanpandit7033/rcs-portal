"""Campaign models for bulk RCS messaging dispatch, tracking, and contacts."""
from decimal import Decimal
import uuid
from django.conf import settings
from django.db import models
from accounts.managers import RoleScopedQuerySetMixin
from messaging.models import Message


class CampaignQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    user_field = "user"


class CampaignManager(models.Manager.from_queryset(CampaignQuerySet)):
    def for_user(self, user):
        return self.get_queryset().for_user(user)


class Campaign(models.Model):
    """Bulk RCS campaign managing contact parsing, balance reservation, and batch dispatch."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PARSING = "parsing", "Parsing File"
        PARSED = "parsed", "File Parsed"
        SCHEDULED = "scheduled", "Scheduled"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="campaigns",
    )
    name = models.CharField("Campaign Name", max_length=150)
    sender_profile = models.ForeignKey(
        "wallet.SenderProfile",
        on_delete=models.PROTECT,
        related_name="campaigns",
    )
    message_type = models.CharField(
        "Message Type",
        max_length=20,
        choices=Message.MessageType.choices,
        default=Message.MessageType.PROMOTIONAL,
    )
    content_type = models.CharField(
        "Content Type",
        max_length=20,
        choices=Message.ContentType.choices,
        default=Message.ContentType.TEMPLATE,
    )
    template = models.ForeignKey(
        "templates_mgmt.Template",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )
    custom_content = models.JSONField(
        "Custom Content Payload",
        default=dict,
        blank=True,
        help_text="Custom text or cards if not utilizing pre-approved template",
    )
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    # Auditing and metrics
    total = models.PositiveIntegerField("Total Rows", default=0)
    valid_count = models.PositiveIntegerField("Valid Contacts", default=0)
    invalid_count = models.PositiveIntegerField("Invalid Contacts", default=0)
    duplicate_count = models.PositiveIntegerField("Duplicate Contacts", default=0)
    processed_count = models.PositiveIntegerField("Processed Contacts", default=0)
    delivered_count = models.PositiveIntegerField("Delivered Count", default=0)
    failed_count = models.PositiveIntegerField("Failed Count", default=0)

    # Financial reservation
    rate_applied = models.DecimalField(
        "Rate Applied (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
    )
    estimated_cost = models.DecimalField(
        "Estimated Cost (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
    )
    reserved_amount = models.DecimalField(
        "Reserved Amount (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
    )

    # Scheduling & Timing
    scheduled_at = models.DateTimeField("Scheduled At", null=True, blank=True, db_index=True)
    started_at = models.DateTimeField("Started At", null=True, blank=True)
    completed_at = models.DateTimeField("Completed At", null=True, blank=True)

    # File storage
    file = models.FileField(upload_to="campaigns/uploads/", null=True, blank=True)
    result_file = models.FileField(upload_to="campaigns/results/", null=True, blank=True)
    column_mapping = models.JSONField(
        "Column Mapping",
        default=dict,
        blank=True,
        help_text="Mapping from CSV/XLSX columns to mobile and template placeholders",
    )

    error_message = models.TextField("Error Message", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CampaignManager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Campaign"
        verbose_name_plural = "Campaigns"

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def progress_pct(self) -> int:
        """Calculate live percentage of processed contacts."""
        if not self.valid_count or self.valid_count == 0:
            return 0
        return min(100, int((self.processed_count / self.valid_count) * 100))


class CampaignContact(models.Model):
    """Individual contact row within a bulk campaign."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        DISPATCHED = "dispatched", "Dispatched"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        INVALID = "invalid", "Invalid"
        DUPLICATE = "duplicate", "Duplicate"
        CANCELLED = "cancelled", "Cancelled"

    id = models.BigAutoField(primary_key=True)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    number = models.CharField("Recipient Number", max_length=20, db_index=True)
    params = models.JSONField("Contact Params", default=dict, blank=True)
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    message = models.ForeignKey(
        "messaging.Message",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="campaign_contacts",
    )
    error_message = models.TextField("Error Message", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["campaign", "number"]),
        ]

    def __str__(self):
        return f"{self.number} ({self.get_status_display()})"
