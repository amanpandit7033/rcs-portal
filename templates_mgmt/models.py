"""RCS Template and MediaFile models."""
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from accounts.managers import RoleScopedQuerySetMixin

name_regex_validator = RegexValidator(
    regex=r"^[a-zA-Z0-9_]+$",
    message="Template name must be alphanumeric and underscores only, with no spaces.",
)


class TemplateQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    user_field = "owner"


class TemplateManager(models.Manager.from_queryset(TemplateQuerySet)):
    def for_user(self, user):
        return self.get_queryset().for_user(user)


class MediaFile(models.Model):
    """Media assets uploaded for RCS cards, carousels, and rich documents."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="media_files",
    )
    file = models.FileField(upload_to="rcs_media/%Y/%m/%d/")
    public_url = models.URLField("Public URL", max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Media File"
        verbose_name_plural = "Media Files"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.owner.email} - {self.file.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.public_url and self.file:
            # Generate file URL
            self.public_url = self.file.url
            super().save(update_fields=["public_url"])


class Template(models.Model):
    """RCS Message Template supporting Text, Media, Rich Cards, and Carousels."""

    class TemplateType(models.TextChoices):
        TEXT_MESSAGE = "text_message", "Text Message"
        TEXT_MESSAGE_WITH_MEDIA = "text_message_with_media", "Text with Media"
        RICH_CARD = "rich_card", "Rich Card (Standalone)"
        CAROUSEL = "carousel", "Carousel"

    class MessageType(models.TextChoices):
        PROMOTIONAL = "PROMOTIONAL", "Promotional"
        TRANSACTIONAL = "TRANSACTIONAL", "Transactional"
        OTP = "OTP", "OTP"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rcs_templates",
    )
    name = models.CharField(
        "Template Name",
        max_length=100,
        unique=True,
        validators=[name_regex_validator],
        help_text="Unique name containing letters, digits, and underscores only.",
        db_index=True,
    )
    template_type = models.CharField(
        "Template Type",
        max_length=30,
        choices=TemplateType.choices,
        db_index=True,
    )
    message_type = models.CharField(
        "Message Type",
        max_length=20,
        choices=MessageType.choices,
        db_index=True,
    )
    sender_profile = models.ForeignKey(
        "wallet.SenderProfile",
        on_delete=models.PROTECT,
        related_name="templates",
    )
    payload = models.JSONField(
        "Template Payload",
        default=dict,
        help_text="Structured JSON conforming to OneXtel specification.",
    )
    status = models.CharField(
        "Approval Status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    onextel_status_raw = models.CharField(
        "OneXtel Raw Status",
        max_length=100,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField("Last Synced At", null=True, blank=True)

    objects = TemplateManager()

    class Meta:
        verbose_name = "RCS Template"
        verbose_name_plural = "RCS Templates"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_template_type_display()}) - {self.get_status_display()}"

    @property
    def is_approved(self) -> bool:
        """Rule: A template can only be used for sending when approved."""
        return self.status == self.Status.APPROVED

    @property
    def detected_placeholders(self):
        """Scans payload text fields and returns unique placeholders like ['name', 'amount']."""
        content = ""
        payload = self.payload or {}

        if "textMessageContent" in payload:
            content += " " + str(payload.get("textMessageContent", ""))

        stand_alone = payload.get("standAlone") or {}
        content += " " + str(stand_alone.get("cardTitle", ""))
        content += " " + str(stand_alone.get("cardDescription", ""))

        for card in payload.get("carouselList", []):
            content += " " + str(card.get("cardTitle", ""))
            content += " " + str(card.get("cardDescription", ""))

        # Match tokens like [name] or [custom_param]
        matches = re.findall(r"\[([a-zA-Z0-9_]+)\]", content)
        return list(dict.fromkeys(matches))
