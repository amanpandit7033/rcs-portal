"""URLs for wallet and billing operations."""
from django.urls import path
from django.views.generic import RedirectView
from .views import (
    AssignCreditView,
    ManageRatePlansView,
    ManageSenderProfilesView,
    ToggleSenderProfileActiveView,
    DeleteSenderProfileView,
    WalletStatementView,
)

app_name = "wallet"

urlpatterns = [
    path("statement/", WalletStatementView.as_view(), name="statement"),
    path("assign-credit/", AssignCreditView.as_view(), name="assign_credit"),
    path("transfer/", RedirectView.as_view(pattern_name="wallet:assign_credit", permanent=False), name="transfer"),
    path("rates/<int:user_id>/", ManageRatePlansView.as_view(), name="manage_rates"),
    path("sender-profiles/<int:user_id>/", ManageSenderProfilesView.as_view(), name="sender_profiles"),
    path("sender-profiles/toggle/<int:profile_id>/", ToggleSenderProfileActiveView.as_view(), name="toggle_sender_profile"),
    path("sender-profiles/delete/<int:profile_id>/", DeleteSenderProfileView.as_view(), name="delete_sender_profile"),
    # URL aliases for senders
    path("senders/<int:user_id>/", ManageSenderProfilesView.as_view(), name="senders_alias"),
    path("senders/toggle/<int:profile_id>/", ToggleSenderProfileActiveView.as_view(), name="toggle_senders_alias"),
    path("senders/delete/<int:profile_id>/", DeleteSenderProfileView.as_view(), name="delete_senders_alias"),
]

