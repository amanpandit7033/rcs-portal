"""Django admin configuration for wallet models."""
from django.contrib import admin
from .models import RatePlan, SenderProfile, Wallet, WalletTransaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "formatted_balance", "currency", "updated_at")
    search_fields = ("user__email", "user__company_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    """Append-only transaction ledger: updates and deletions are disabled in admin."""

    list_display = (
        "created_at",
        "wallet",
        "type",
        "formatted_amount",
        "formatted_balance_after",
        "reference_type",
        "created_by",
    )
    list_filter = ("type", "reference_type", "created_at")
    search_fields = ("wallet__user__email", "remarks", "reference_id")
    readonly_fields = [f.name for f in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        # Ledger rows should only be created via the service layer
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RatePlan)
class RatePlanAdmin(admin.ModelAdmin):
    list_display = ("user", "message_type", "rate", "cost_rate", "valid_from")
    list_filter = ("message_type",)
    search_fields = ("user__email",)


@admin.register(SenderProfile)
class SenderProfileAdmin(admin.ModelAdmin):
    list_display = ("sender_profile_name", "sender_id", "user", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("sender_profile_name", "sender_id", "user__email")
