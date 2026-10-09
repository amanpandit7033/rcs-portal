"""URLs for analytics, reporting, and streaming CSV exports."""
from django.urls import path

from .views import (
    AdminDLRLogListView,
    AdminOverviewReportView,
    MessageLogExportCSVView,
    ResellerMarginReportView,
    UserUsageReportView,
)

app_name = "reports"

urlpatterns = [
    path("export/messages/", MessageLogExportCSVView.as_view(), name="export_messages_csv"),
    path("export/messages/csv/", MessageLogExportCSVView.as_view(), name="message_logs_export_csv"),
    path("usage/", UserUsageReportView.as_view(), name="user_usage"),
    path("reseller/margin/", ResellerMarginReportView.as_view(), name="reseller_margin"),
    path("admin/overview/", AdminOverviewReportView.as_view(), name="admin_overview"),
    path("admin/dlr-logs/", AdminDLRLogListView.as_view(), name="admin_dlr_logs"),
]
