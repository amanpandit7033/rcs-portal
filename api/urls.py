"""URLs for public API v1 endpoints and developer settings."""
from django.urls import path
from .views import (
    APIDocumentationView,
    APISettingsView,
    BalanceAPIView,
    BulkMessageSendAPIView,
    MessageListAPIView,
    MessageSendAPIView,
    MessageStatusAPIView,
    PostmanCollectionExportView,
    TemplateDetailAPIView,
    TemplateListCreateAPIView,
)

app_name = "api"

urlpatterns = [
    # Developer Portal Documentation & Settings (Web UI)
    path("documentation/", APIDocumentationView.as_view(), name="documentation"),
    path("settings/", APISettingsView.as_view(), name="settings"),

    # API v1 Endpoints
    path("v1/messages/send/", MessageSendAPIView.as_view(), name="v1_message_send"),
    path("v1/messages/bulk/", BulkMessageSendAPIView.as_view(), name="v1_message_bulk"),
    path("v1/messages/<str:id>/status/", MessageStatusAPIView.as_view(), name="v1_message_status"),
    path("v1/messages/", MessageListAPIView.as_view(), name="v1_message_list"),
    path("v1/templates/", TemplateListCreateAPIView.as_view(), name="v1_template_list_create"),
    path("v1/templates/<int:pk>/", TemplateDetailAPIView.as_view(), name="v1_template_detail"),
    path("v1/balance/", BalanceAPIView.as_view(), name="v1_balance"),
    path("v1/postman/", PostmanCollectionExportView.as_view(), name="v1_postman_export"),
]
