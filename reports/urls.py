"""URLs for analytics, reporting, and streaming CSV exports."""
from django.urls import path

from .views import (
    AdminCampaignReportListView,
    AdminDLRLogListView,
    LiveStatusView,
    MessageLogExportCSVView,
    MISBillingView,
    ResellerMarginReportView,
    UserUsageReportView,
)

app_name = "reports"

urlpatterns = [
    path("live-status/", LiveStatusView.as_view(), name="live_status"),
    path("mis-billing/", MISBillingView.as_view(), name="mis_billing"),
    path("export/messages/", MessageLogExportCSVView.as_view(), name="export_messages_csv"),
    path("export/messages/csv/", MessageLogExportCSVView.as_view(), name="message_logs_export_csv"),
    path("usage/", UserUsageReportView.as_view(), name="user_usage"),
    path("reseller/margin/", ResellerMarginReportView.as_view(), name="reseller_margin"),
    path("admin/dlr-logs/", AdminDLRLogListView.as_view(), name="admin_dlr_logs"),
    path("admin/campaigns/", AdminCampaignReportListView.as_view(), name="admin_campaign_reports"),
]
