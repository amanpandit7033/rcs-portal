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
