"""URL routes for client onboarding and superadmin operator management."""
from django.urls import path
from .views import (
    AdminOnboardingDetailView,
    AdminOnboardingListView,
    ClientOnboardingCreateView,
    ClientOnboardingDetailView,
    ClientOnboardingListView,
    DownloadAllApplicationsExcelView,
    DownloadApplicationZipView,
)

app_name = "onboarding"

urlpatterns = [
    # Client Routes
    path("", ClientOnboardingListView.as_view(), name="client_list"),
    path("apply/", ClientOnboardingCreateView.as_view(), name="client_apply"),
    path("<int:pk>/", ClientOnboardingDetailView.as_view(), name="client_detail"),
    path("<int:pk>/download-zip/", DownloadApplicationZipView.as_view(), name="download_zip"),

    # Superadmin Management Routes
    path("admin/applications/", AdminOnboardingListView.as_view(), name="admin_list"),
    path("admin/applications/<int:pk>/", AdminOnboardingDetailView.as_view(), name="admin_detail"),
    path("admin/export-excel/", DownloadAllApplicationsExcelView.as_view(), name="export_excel"),
]
