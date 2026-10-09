"""URLs for RCS message dispatch, tracking, and logs."""
from django.urls import path

from .views import (
    MessageDetailView,
    MessageLogView,
    UserRatePreviewAjaxView,
)
from .webhooks import OneXtelDLRWebhookView

app_name = "messaging"

urlpatterns = [
    path("logs/", MessageLogView.as_view(), name="message_logs"),
    path("logs/<uuid:pk>/", MessageDetailView.as_view(), name="message_detail"),
    path("rates/preview/", UserRatePreviewAjaxView.as_view(), name="rate_preview"),
    path("webhook/dlr/", OneXtelDLRWebhookView.as_view(), name="dlr_webhook"),
]
