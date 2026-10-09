"""URLs for wallet and billing operations."""
from django.urls import path
from .views import (
    AdminAdjustWalletView,
    AssignCreditView,
    ManageRatePlansView,
    ManageSenderProfilesView,
    ResellerCreditTransferView,
    ToggleSenderProfileActiveView,
    WalletStatementView,
)

app_name = "wallet"

urlpatterns = [
    path("statement/", WalletStatementView.as_view(), name="statement"),
    path("assign-credit/", AssignCreditView.as_view(), name="assign_credit"),
    path("transfer/", ResellerCreditTransferView.as_view(), name="transfer"),
    path("adjust/<int:user_id>/", AdminAdjustWalletView.as_view(), name="adjust"),
    path("rates/<int:user_id>/", ManageRatePlansView.as_view(), name="manage_rates"),
    path("sender-profiles/<int:user_id>/", ManageSenderProfilesView.as_view(), name="sender_profiles"),
    path("sender-profiles/toggle/<int:profile_id>/", ToggleSenderProfileActiveView.as_view(), name="toggle_sender_profile"),
]
