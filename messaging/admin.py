"""Django admin configuration for RCS messaging and DLR logs."""
from django.contrib import admin
from .models import DLRLog, Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "user",
        "sender_profile",
        "message_type",
        "content_type",
        "status",
        "onextel_message_id",
        "cost",
        "is_refunded",
        "created_at",
    )
    list_filter = ("status", "message_type", "content_type", "is_refunded", "created_at")
    search_fields = (
        "id",
        "recipient",
        "onextel_message_id",
        "user__email",
        "user__username",
        "sender_profile__sender_profile_name",
    )
    readonly_fields = (
        "id",
        "created_at",
        "submitted_at",
        "delivered_at",
        "read_at",
    )
    fieldsets = (
        ("Routing & Identity", {
            "fields": ("id", "user", "sender_profile", "recipient", "prefix", "campaign")
        }),
        ("Content & Classification", {
            "fields": ("message_type", "content_type", "template", "custom_params", "payload")
        }),
        ("Carrier Delivery Status", {
            "fields": (
                "status",
                "onextel_message_id",
                "failure_reason",
                "submitted_at",
                "delivered_at",
                "read_at",
            )
        }),
        ("Financials & Audit", {
            "fields": ("cost", "rate_applied", "is_refunded", "created_at")
        }),
    )


@admin.register(DLRLog)
class DLRLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "onextel_message_id",
        "status_extracted",
        "processed",
        "message",
        "source_ip",
        "received_at",
    )
    list_filter = ("processed", "status_extracted", "received_at")
    search_fields = ("onextel_message_id", "status_extracted", "source_ip", "error_message")
    readonly_fields = ("id", "received_at", "processed_at", "payload")
    fieldsets = (
        ("Overview", {
            "fields": ("id", "onextel_message_id", "status_extracted", "message", "processed", "processed_at", "source_ip")
        }),
        ("Processing Diagnostics", {
            "fields": ("error_message", "received_at")
        }),
        ("Carrier Raw JSON Payload", {
            "fields": ("payload",),
            "classes": ("collapse",),
        }),
    )
