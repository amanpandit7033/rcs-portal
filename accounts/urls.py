"""URLs for accounts authentication, dashboards, and management."""
from django.urls import path
from .views import (
    AdminDashboardView,
    ClientCreateView,
    ClientEditView,
    ClientListView,
    ComingSoonView,
    DashboardRedirectView,
    ImpersonateClientView,
    ResellerDashboardView,
    StopImpersonationView,
    SystemSettingsView,
    ToggleClientActiveView,
    ToggleUserActiveView,
    UserCreateView,
    UserDashboardView,
    UserEditView,
    UserListView,
    UserLoginView,
    UserLogoutView,
    UserPasswordResetCompleteView,
    UserPasswordResetConfirmView,
    UserPasswordResetDoneView,
    UserPasswordResetView,
    UserProfileView,
)

app_name = "accounts"

urlpatterns = [
    # Auth
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),

    # Dashboards, Profile & Settings
    path("dashboard/", DashboardRedirectView.as_view(), name="dashboard_redirect"),
    path("dashboard/admin/", AdminDashboardView.as_view(), name="admin_dashboard"),
    path("dashboard/reseller/", ResellerDashboardView.as_view(), name="reseller_dashboard"),
    path("dashboard/user/", UserDashboardView.as_view(), name="user_dashboard"),
    path("profile/", UserProfileView.as_view(), name="user_profile"),
    path("settings/", SystemSettingsView.as_view(), name="system_settings"),
    path("coming-soon/", ComingSoonView.as_view(), name="coming_soon"),

    # Admin: Users & Resellers Management
    path("users/", UserListView.as_view(), name="user_list"),
    path("users/create/", UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", UserEditView.as_view(), name="user_edit"),
    path("users/<int:user_id>/toggle-status/", ToggleUserActiveView.as_view(), name="toggle_user_status"),

    # Reseller: Client Management
    path("clients/", ClientListView.as_view(), name="client_list"),
    path("clients/create/", ClientCreateView.as_view(), name="client_create"),
    path("clients/<int:pk>/edit/", ClientEditView.as_view(), name="client_edit"),
    path("clients/<int:client_id>/toggle-status/", ToggleClientActiveView.as_view(), name="toggle_client_status"),

    # Impersonation Flow ("Login as Client")
    path("clients/<int:client_id>/impersonate/", ImpersonateClientView.as_view(), name="impersonate_client"),
    path("impersonate/stop/", StopImpersonationView.as_view(), name="stop_impersonation"),

    # Password Reset
    path("password-reset/", UserPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", UserPasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", UserPasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", UserPasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
