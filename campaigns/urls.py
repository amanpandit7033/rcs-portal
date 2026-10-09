"""URL routing for campaigns app."""
from django.urls import path
from .views import (
    CampaignActionView,
    CampaignCreateView,
    CampaignDetailView,
    CampaignExportCSVView,
    CampaignListView,
    CampaignMappingView,
    CampaignProgressAjaxView,
    CampaignReviewView,
)

app_name = "campaigns"

urlpatterns = [
    path("", CampaignListView.as_view(), name="campaign_list"),
    path("new/", CampaignCreateView.as_view(), name="campaign_create"),
    path("<uuid:campaign_id>/map/", CampaignMappingView.as_view(), name="campaign_mapping"),
    path("<uuid:campaign_id>/review/", CampaignReviewView.as_view(), name="campaign_review"),
    path("<uuid:campaign_id>/", CampaignDetailView.as_view(), name="campaign_detail"),
    path("<uuid:campaign_id>/progress/", CampaignProgressAjaxView.as_view(), name="campaign_progress"),
    path("<uuid:campaign_id>/action/", CampaignActionView.as_view(), name="campaign_action"),
    path("<uuid:campaign_id>/export/", CampaignExportCSVView.as_view(), name="campaign_export_csv"),
]
