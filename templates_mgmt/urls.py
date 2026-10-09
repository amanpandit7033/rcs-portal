"""URLs for RCS templates management."""
from django.urls import path

from .views import (
    MediaUploadAjaxView,
    TemplateCreateView,
    TemplateDetailView,
    TemplateListView,
    TemplateRefreshStatusView,
)

app_name = "templates_mgmt"

urlpatterns = [
    path("", TemplateListView.as_view(), name="template_list"),
    path("create/", TemplateCreateView.as_view(), name="template_create"),
    path("<int:pk>/", TemplateDetailView.as_view(), name="template_detail"),
    path("<int:pk>/refresh/", TemplateRefreshStatusView.as_view(), name="template_refresh"),
    path("media/upload/", MediaUploadAjaxView.as_view(), name="media_upload"),
]
