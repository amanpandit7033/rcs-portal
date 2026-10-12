"""Django admin configuration for RCS templates and media files."""
from django.contrib import admin
from .models import MediaFile, Template


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "template_type",
        "message_type",
        "sender_profile",
        "status",
        "last_synced_at",
        "created_at",
    )
    list_filter = ("status", "template_type", "message_type", "created_at")
    search_fields = ("name", "owner__email", "owner__username", "sender_profile__sender_profile_name", "sender_profile__sender_id")
    readonly_fields = ("created_at", "updated_at", "last_synced_at", "onextel_status_raw")
    fieldsets = (
        ("Basic Information", {
            "fields": ("name", "owner", "sender_profile", "template_type", "message_type", "status")
        }),
        ("Carrier Synchronization", {
            "fields": ("onextel_status_raw", "last_synced_at", "created_at", "updated_at")
        }),
        ("Template Definition Payload", {
            "fields": ("payload",),
            "classes": ("collapse",),
        }),
    )


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    list_display = ("file", "owner", "public_url", "created_at")
    search_fields = ("owner__email", "owner__username", "file", "public_url")
    readonly_fields = ("created_at", "public_url")
