from django.urls import path
from .views import (
    CampaignActionView,
    CampaignCreateView,
    CampaignDetailView,
    CampaignExportCSVView,
    CampaignListView,
    CampaignProgressAjaxView,
    ContactGroupCreateView,
    ContactGroupDeleteContactView,
    ContactGroupDeleteView,
    ContactGroupDetailView,
    ContactGroupExportView,
    ContactGroupListView,
    ContactGroupNumbersAjaxView,
    ContactGroupUpdateView,
)

app_name = "campaigns"

urlpatterns = [
    path("", CampaignListView.as_view(), name="campaign_list"),
    path("new/", CampaignCreateView.as_view(), name="campaign_create"),
    path("<uuid:campaign_id>/", CampaignDetailView.as_view(), name="campaign_detail"),
    path("<uuid:campaign_id>/progress/", CampaignProgressAjaxView.as_view(), name="campaign_progress"),
    path("<uuid:campaign_id>/action/", CampaignActionView.as_view(), name="campaign_action"),
    path("<uuid:campaign_id>/export/", CampaignExportCSVView.as_view(), name="campaign_export_csv"),

    # Contact Groups Management
    path("groups/", ContactGroupListView.as_view(), name="group_list"),
    path("groups/create/", ContactGroupCreateView.as_view(), name="group_create"),
    path("groups/<int:pk>/", ContactGroupDetailView.as_view(), name="group_detail"),
    path("groups/<int:pk>/edit/", ContactGroupUpdateView.as_view(), name="group_edit"),
    path("groups/<int:pk>/delete/", ContactGroupDeleteView.as_view(), name="group_delete"),
    path("groups/<int:pk>/delete-contact/<int:contact_id>/", ContactGroupDeleteContactView.as_view(), name="group_delete_contact"),
    path("groups/<int:pk>/export/", ContactGroupExportView.as_view(), name="group_export"),
    path("groups/<int:pk>/numbers/", ContactGroupNumbersAjaxView.as_view(), name="group_numbers_ajax"),
]
