"""Django admin configuration for developer API keys and outbound webhooks."""
from django.contrib import admin
from .models import APIKey, ClientWebhook, WebhookForwardLog


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "prefix",
        "user",
        "is_active",
        "rate_limit_rpm",
        "last_used_at",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "prefix", "user__email", "user__username")
    readonly_fields = ("prefix", "hashed_key", "last_used_at", "created_at", "updated_at")
    fieldsets = (
        ("Key Details", {
            "fields": ("name", "user", "prefix", "is_active", "rate_limit_rpm")
        }),
        ("Security & Restriction", {
            "fields": ("ip_whitelist", "hashed_key")
        }),
        ("Audit", {
            "fields": ("last_used_at", "created_at", "updated_at")
        }),
    )


@admin.register(ClientWebhook)
class ClientWebhookAdmin(admin.ModelAdmin):
    list_display = ("user", "url", "is_active", "updated_at", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("user__email", "user__username", "url")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WebhookForwardLog)
class WebhookForwardLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "message_id",
        "url",
        "status_code",
        "success",
        "attempt",
        "created_at",
    )
    list_filter = ("success", "status_code", "created_at")
    search_fields = ("message_id", "url", "user__email", "error_message")
    readonly_fields = [f.name for f in WebhookForwardLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
