"""Django admin configuration for bot onboarding applications."""
from django.contrib import admin
from .models import OnboardingApplication


@admin.register(OnboardingApplication)
class OnboardingApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "brand_name",
        "bot_name",
        "user",
        "status",
        "operator_name",
        "primary_phone",
        "primary_email",
        "created_at",
    )
    list_filter = ("status", "operator_name", "created_at")
    search_fields = (
        "brand_name",
        "bot_name",
        "user__email",
        "user__username",
        "primary_phone",
        "primary_email",
        "operator_reference_id",
    )
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Application Status & Operator Assignment", {
            "fields": ("status", "operator_name", "operator_reference_id", "admin_notes")
        }),
        ("Applicant & Brand Identity", {
            "fields": (
                "user",
                "brand_name",
                "bot_name",
                "bot_message_type",
                "brand_spoc_name",
                "brand_spoc_designation",
                "brand_spoc_email",
            )
        }),
        ("Design & Profile Assets", {
            "fields": (
                "bot_logo",
                "banner_image",
                "short_description",
                "color_code",
                "languages_supported",
            )
        }),
        ("Contact & Verification", {
            "fields": (
                "primary_phone",
                "primary_phone_label",
                "primary_email",
                "primary_email_label",
                "primary_website",
                "primary_website_label",
                "terms_conditions_url",
                "privacy_url",
            )
        }),
        ("KYC & Compliance Documents", {
            "fields": ("gst_certificate", "pan_card", "opt_in_screenshot", "created_at", "updated_at")
        }),
    )
