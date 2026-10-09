"""Models for Client Bot Onboarding."""
from django.conf import settings
from django.db import models


class OnboardingApplication(models.Model):
    """Client Bot Onboarding Application for Carrier/Operator approval."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted / Pending Review"
        FORWARDED_TO_OPERATOR = "forwarded_to_operator", "Forwarded to Operator"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="onboarding_applications",
        help_text="The client/user who submitted this application",
    )

    # Status tracking
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )

    # 1. Bot Message Type (Multi-select: Promotional, Transactional, OTP)
    bot_message_type = models.CharField(
        "Bot Message Type",
        max_length=255,
        help_text="Comma-separated selected message types: Promotional, Transactional, OTP",
    )

    # 2. Bot & Brand Identity
    brand_name = models.CharField("Brand Name", max_length=255)
    bot_name = models.CharField("Bot Name", max_length=40, help_text="At least 1 alpha character. Max of 40 characters.")

    # Brand SPOC Details
    brand_spoc_name = models.CharField("Brand SPOC Name", max_length=255, default="", blank=True)
    brand_spoc_designation = models.CharField("Brand SPOC Designation", max_length=255, blank=True, default="")
    brand_spoc_email = models.EmailField("Brand SPOC Email", blank=True, default="")

    # KYC & Verification Documents
    gst_certificate = models.FileField(
        "GST Certificate",
        upload_to="onboarding/gst/",
        blank=True,
        null=True,
        help_text="Upload 1 supported file: PDF. Max 10 MB.",
    )
    pan_card = models.FileField(
        "PAN Card",
        upload_to="onboarding/pan/",
        blank=True,
        null=True,
        help_text="Upload 1 supported file. Max 10 MB.",
    )

    # 9-10. Visual Assets
    bot_logo = models.FileField(
        "Bot Logo",
        upload_to="onboarding/logos/",
        help_text="Multipart file Max length 50 KB, 224 x 224 px. Extensions: JPG, JPEG, PNG",
    )
    banner_image = models.FileField(
        "Banner Image",
        upload_to="onboarding/banners/",
        help_text="File Size Max: 200 KB, Dimensions: 1440 x 448 px. Extensions: JPG, JPEG, PNG",
    )

    # 11-12. Profile Styling & Description
    short_description = models.CharField("Short Description", max_length=100, help_text="Max of 100 characters")
    color_code = models.CharField("Colour Code", max_length=20, default="#2563EB", help_text="Hex color code, e.g. #2563EB")

    # 13-14. Phone
    primary_phone = models.CharField("Primary Phone Number", max_length=30, help_text="Valid phone number with country code")
    primary_phone_label = models.CharField("Label for primary phone number", max_length=25, default="Phone")

    # 15-16. Email
    primary_email = models.EmailField("Primary Email ID", help_text="Valid Email ID")
    primary_email_label = models.CharField("Label for primary email id", max_length=25, default="Email")

    # 17-18. Website
    primary_website = models.URLField("Primary Website", help_text="Valid website URL")
    primary_website_label = models.CharField("Label for primary website", max_length=25, default="Website")

    # 19-20. Legal URLs
    terms_conditions_url = models.URLField("Terms and Conditions URL", max_length=2048)
    privacy_url = models.URLField("Privacy URL", max_length=2048)

    # 21. Languages
    languages_supported = models.CharField("Languages Supported", max_length=255, default="English", help_text="Ex: English, Hindi")

    # 22. Opt-in Verification Screenshot
    opt_in_screenshot = models.FileField(
        "Opt-in Screenshot",
        upload_to="onboarding/opt_in/",
        help_text="Screenshot showcasing how opt-in is collected from customers for carrier verification.",
    )

    # Administrative metadata
    operator_name = models.CharField("Operator / Carrier", max_length=100, default="Vodafone / VI", blank=True)
    operator_reference_id = models.CharField("Operator Reference ID", max_length=100, blank=True)
    admin_notes = models.TextField("Admin Notes / Remarks", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Onboarding Application"
        verbose_name_plural = "Onboarding Applications"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.brand_name} ({self.bot_name}) - @{self.user.username}"

    def get_message_types_list(self):
        if not self.bot_message_type:
            return []
        return [t.strip() for t in self.bot_message_type.split(",") if t.strip()]
