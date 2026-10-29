"""Admin interface configuration for accounts."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "email",
        "role",
        "company_name",
        "parent_reseller",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = ("role", "is_active", "is_staff", "parent_reseller")
    search_fields = ("email", "first_name", "last_name", "company_name", "phone", "GSTIN")
    ordering = ("-date_joined",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Profile Info",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "company_name",
                    "phone",
                    "GSTIN",
                    "state",
                )
            },
        ),
        (
            "Role & Hierarchy",
            {
                "fields": (
                    "role",
                    "parent_reseller",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "allow_refund",
                    "can_view_live_status",
                    "can_use_low_price",
                    "is_low_price_applied",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "role",
                    "company_name",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                ),
            },
        ),
    )


from .models import WhiteLabel, WhitelistNumber, UserKYC, LoginHistory


@admin.register(WhiteLabel)
class WhiteLabelAdmin(admin.ModelAdmin):
    list_display = ("company_name", "pointed_subdomain", "website_url", "support_number", "support_email", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("company_name", "pointed_subdomain", "support_email", "support_number")


@admin.register(WhitelistNumber)
class WhitelistNumberAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_number", "description", "created_by", "created_at")
    list_filter = ("user", "created_at")
    search_fields = ("phone_number", "description", "user__username")


@admin.register(UserKYC)
class UserKYCAdmin(admin.ModelAdmin):
    list_display = ("user", "company_name", "person_name", "phone", "email", "pan_card", "status", "updated_at")
    list_filter = ("status", "created_at", "updated_at")
    search_fields = ("user__username", "company_name", "person_name", "email", "phone", "pan_card", "aadhar", "gst_number")


@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "ip_address", "device", "timestamp")
    list_filter = ("timestamp", "user__role")
    search_fields = ("user__username", "user__email", "ip_address", "device", "user_agent")
    readonly_fields = ("user", "ip_address", "user_agent", "device", "timestamp")


