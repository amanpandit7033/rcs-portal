"""Django admin configuration for bulk RCS campaigns and contacts."""
from django.contrib import admin
from .models import Campaign, CampaignContact, ContactGroup, GroupContact


class CampaignContactInline(admin.TabularInline):
    model = CampaignContact
    extra = 0
    fields = ("number", "status", "message", "created_at")
    readonly_fields = ("number", "status", "message", "created_at")
    can_delete = False
    show_change_link = True
    max_num = 50


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "sender_profile",
        "status",
        "total",
        "valid_count",
        "processed_count",
        "progress_pct",
        "estimated_cost",
        "created_at",
    )
    list_filter = ("status", "message_type", "created_at")
    search_fields = ("name", "id", "user__email", "user__username", "sender_profile__sender_profile_name")
    readonly_fields = (
        "id",
        "total",
        "valid_count",
        "invalid_count",
        "duplicate_count",
        "processed_count",
        "estimated_cost",
        "reserved_amount",
        "rate_applied",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Campaign Identity", {
            "fields": ("id", "name", "user", "sender_profile", "status", "template", "message_type")
        }),
        ("Metrics & Processing", {
            "fields": (
                "total",
                "valid_count",
                "invalid_count",
                "duplicate_count",
                "processed_count",
                "started_at",
                "completed_at",
                "scheduled_at",
            )
        }),
        ("Financials", {
            "fields": ("estimated_cost", "reserved_amount", "rate_applied")
        }),
        ("File & Column Mapping", {
            "fields": ("file", "column_mapping", "error_message", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    inlines = [CampaignContactInline]


@admin.register(CampaignContact)
class CampaignContactAdmin(admin.ModelAdmin):
    list_display = ("id", "campaign", "number", "status", "message", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("number", "campaign__name", "campaign__id")
    readonly_fields = ("created_at",)


class GroupContactInline(admin.TabularInline):
    model = GroupContact
    extra = 0
    fields = ("mobile_number", "name", "created_at")
    readonly_fields = ("created_at",)
    max_num = 100


@admin.register(ContactGroup)
class ContactGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "total_contacts", "created_at", "updated_at")
    search_fields = ("name", "user__email", "user__username")
    list_filter = ("created_at",)
    inlines = [GroupContactInline]


@admin.register(GroupContact)
class GroupContactAdmin(admin.ModelAdmin):
    list_display = ("mobile_number", "name", "group", "created_at")
    search_fields = ("mobile_number", "name", "group__name")
    list_filter = ("created_at",)
