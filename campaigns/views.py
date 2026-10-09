"""Views for bulk campaign creation, column mapping, review, HTMX progress tracking, and actions."""
from datetime import datetime
from decimal import Decimal
import logging
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View

from accounts.mixins import RoleRequiredMixin
from reports.csv_stream import stream_csv_response
from wallet.models import SenderProfile
from wallet.services import InsufficientBalanceError
from .models import Campaign, CampaignContact
from .services import (
    calculate_campaign_estimate,
    cancel_campaign,
    pause_campaign,
    reserve_campaign_funds,
    resume_campaign,
)
from .tasks import parse_campaign_file_task, run_campaign_task
from .utils import preview_file_rows

logger = logging.getLogger(__name__)


class CampaignListView(RoleRequiredMixin, View):
    """List all bulk campaigns scoped to user role with search and status filtering."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "campaigns/campaign_list.html"

    def get(self, request):
        qs = Campaign.objects.for_user(request.user).select_related(
            "user", "sender_profile", "template"
        ).order_by("-created_at")

        status = request.GET.get("status")
        q = request.GET.get("q")

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(name__icontains=q)

        paginator = Paginator(qs, 15)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        return render(
            request,
            self.template_name,
            {
                "campaigns": page_obj,
                "selected_status": status or "",
                "search_query": q or "",
                "page_title": "Bulk RCS Campaigns",
            },
        )


class CampaignCreateView(RoleRequiredMixin, View):
    """Directly configure, preview template, parse recipients, reserve funds, and launch campaign."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "campaigns/campaign_create.html"

    def get(self, request):
        if request.user.is_admin or request.user.is_superuser:
            admin_profiles = SenderProfile.objects.filter(user=request.user, is_active=True)
            sender_profiles = admin_profiles if admin_profiles.exists() else SenderProfile.objects.filter(is_active=True)
        else:
            sender_profiles = SenderProfile.objects.filter(user=request.user, is_active=True)

        from templates_mgmt.models import Template

        templates = (
            Template.objects.for_user(request.user)
            .filter(status=Template.Status.APPROVED)
            .select_related("sender_profile")
            .order_by("-created_at")
        )

        import json

        templates_data = {}
        for t in templates:
            payload = t.payload or {}
            stand_alone = payload.get("standAlone") or {}
            templates_data[str(t.id)] = {
                "id": t.id,
                "name": t.name,
                "template_type": t.template_type,
                "message_type": t.message_type,
                "sender_profile_id": str(t.sender_profile_id) if t.sender_profile_id else "",
                "sender_profile_name": t.sender_profile.sender_profile_name if t.sender_profile else "",
                "text_message_content": payload.get("textMessageContent", ""),
                "document_url": payload.get("documentUrl") or payload.get("mediaUrl") or "",
                "media_order": payload.get("mediaOrder") or "media_at_top",
                "card_title": stand_alone.get("cardTitle", ""),
                "card_description": stand_alone.get("cardDescription", ""),
                "card_media_url": stand_alone.get("mediaUrl", ""),
                "card_orientation": stand_alone.get("cardOrientation", "VERTICAL"),
                "card_height": stand_alone.get("cardHeight", "MEDIUM_HEIGHT"),
                "carousel_cards": [
                    {
                        "cardTitle": c.get("cardTitle", ""),
                        "cardDescription": c.get("cardDescription", ""),
                        "mediaUrl": c.get("mediaUrl", ""),
                        "suggestions": c.get("suggestions", []),
                    }
                    for c in payload.get("carouselList", [])
                ],
                "carousel_width": payload.get("carouselWidth", "MEDIUM_WIDTH"),
                "carousel_height": payload.get("carouselHeight", "MEDIUM_HEIGHT"),
                "suggestions": payload.get("suggestions") or stand_alone.get("suggestions") or [],
                "placeholders": t.detected_placeholders,
            }

        user_wallet = getattr(request.user, "wallet", None)
        current_balance = user_wallet.balance if user_wallet else Decimal("0.0000")

        return render(
            request,
            self.template_name,
            {
                "sender_profiles": sender_profiles,
                "templates": templates,
                "templates_json": json.dumps(templates_data),
                "current_balance": current_balance,
                "page_title": "Create & Send Campaign",
            },
        )

    def post(self, request):
        name = request.POST.get("name", "").strip()
        sender_profile_id = request.POST.get("sender_profile")
        message_type = request.POST.get("message_type", "PROMOTIONAL")
        template_id = request.POST.get("template")
        input_mode = request.POST.get("input_mode", "paste")
        recipient_numbers_raw = request.POST.get("recipient_numbers", "").strip()
        uploaded_file = request.FILES.get("file")
        action_type = request.POST.get("action_type", "now")
        scheduled_datetime_str = request.POST.get("scheduled_at", "").strip()

        if not name or not sender_profile_id:
            messages.error(request, "Please provide campaign name and sender profile.")
            return redirect("campaigns:campaign_create")

        import re
        from django.core.files.base import ContentFile
        from .utils import auto_map_placeholders, detect_mobile_column

        pasted_numbers = []
        if recipient_numbers_raw:
            raw_lines = re.split(r"[\r\n,;]+", recipient_numbers_raw)
            pasted_numbers = [line.strip() for line in raw_lines if line.strip()]

        campaign_file = None
        mobile_col = "mobile_number"
        headers = []

        if input_mode == "file" and uploaded_file:
            campaign_file = uploaded_file
        elif pasted_numbers:
            csv_content = "mobile_number\n" + "\n".join(pasted_numbers) + "\n"
            file_name = f"recipients_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            campaign_file = ContentFile(csv_content.encode("utf-8"), name=file_name)
        elif uploaded_file:
            campaign_file = uploaded_file
        else:
            messages.error(request, "Please enter recipient mobile numbers or choose a recipient file.")
            return redirect("campaigns:campaign_create")

        if request.user.is_admin or request.user.is_superuser:
            sender_profile = get_object_or_404(SenderProfile, id=sender_profile_id, is_active=True)
        else:
            sender_profile = get_object_or_404(SenderProfile, id=sender_profile_id, user=request.user, is_active=True)

        from templates_mgmt.models import Template

        template = None
        if template_id:
            template = get_object_or_404(
                Template.objects.for_user(request.user),
                id=template_id,
            )

        # Create campaign record
        campaign = Campaign.objects.create(
            user=request.user,
            name=name,
            sender_profile=sender_profile,
            message_type=message_type,
            template=template,
            file=campaign_file,
            status=Campaign.Status.PARSING,
        )

        # Inspect headers for file upload
        if input_mode == "file" or (not pasted_numbers and uploaded_file):
            try:
                preview_data = preview_file_rows(campaign.file.path, max_rows=5)
                headers = preview_data.get("headers", [])
                mobile_col = detect_mobile_column(headers)
            except Exception as e:
                logger.warning("Could not preview file headers for campaign %s: %s", campaign.id, e)
                mobile_col = "mobile_number"

        # Build placeholder parameter mapping
        param_map = {}
        if template:
            auto_mapped = auto_map_placeholders(headers if headers else ["mobile_number"], template.detected_placeholders)
            for p in template.detected_placeholders:
                explicit_col = request.POST.get(f"placeholder_{p}", "").strip()
                if explicit_col:
                    param_map[p] = explicit_col
                elif p in auto_mapped:
                    param_map[p] = auto_mapped[p]

        campaign.column_mapping = {
            "mobile_col": mobile_col,
            "param_map": param_map,
        }
        campaign.save(update_fields=["column_mapping"])

        # Directly parse, reserve funds, and queue or schedule
        parse_campaign_file_task(
            str(campaign.id),
            auto_launch=True,
            schedule_datetime=scheduled_datetime_str if action_type == "schedule" else None,
        )

        campaign.refresh_from_db()

        if campaign.status == Campaign.Status.FAILED:
            messages.error(request, campaign.error_message or "Campaign failed to launch.")
        elif campaign.status == Campaign.Status.SCHEDULED:
            messages.success(request, f"Campaign '{campaign.name}' scheduled for {campaign.scheduled_at.strftime('%Y-%m-%d %H:%M')}.")
        else:
            messages.success(request, f"Campaign '{campaign.name}' launched successfully! {campaign.valid_count} recipients queued.")

        return redirect("campaigns:campaign_detail", campaign_id=campaign.id)


class CampaignMappingView(RoleRequiredMixin, View):
    """Step 2: Preview top 10 rows and map columns to recipient mobile and template placeholders."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "campaigns/campaign_mapping.html"

    def get(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)

        try:
            preview_data = preview_file_rows(campaign.file.path, max_rows=10)
        except Exception as e:
            messages.error(request, f"Unable to read uploaded file: {e}")
            return redirect("campaigns:campaign_list")

        placeholders = []
        if campaign.template:
            placeholders = campaign.template.detected_placeholders

        return render(
            request,
            self.template_name,
            {
                "campaign": campaign,
                "headers": preview_data.get("headers", []),
                "preview_rows": preview_data.get("rows", []),
                "placeholders": placeholders,
                "page_title": f"Map Columns - {campaign.name}",
            },
        )

    def post(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)
        mobile_col = request.POST.get("mobile_col", "").strip()

        if not mobile_col:
            messages.error(request, "You must select which column corresponds to Mobile Number.")
            return redirect("campaigns:campaign_mapping", campaign_id=campaign.id)

        # Build param map for template placeholders
        param_map = {}
        if campaign.template:
            for placeholder in campaign.template.detected_placeholders:
                mapped_col = request.POST.get(f"placeholder_{placeholder}", "").strip()
                if mapped_col:
                    param_map[placeholder] = mapped_col

        campaign.column_mapping = {
            "mobile_col": mobile_col,
            "param_map": param_map,
        }
        campaign.status = Campaign.Status.PARSING
        campaign.save(update_fields=["column_mapping", "status", "updated_at"])

        # Kick off background streaming chunk parser
        parse_campaign_file_task.delay(str(campaign.id))
        messages.info(request, "File parsing started in background.")

        return redirect("campaigns:campaign_review", campaign_id=campaign.id)


class CampaignReviewView(RoleRequiredMixin, View):
    """Step 3: Review parsed contacts stats, check wallet balance, reserve funds, and launch or schedule."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "campaigns/campaign_review.html"

    def get(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)
        user_wallet = getattr(request.user, "wallet", None)
        current_balance = user_wallet.balance if user_wallet else Decimal("0.0000")

        # Recalculate estimate if parsed
        if campaign.status == Campaign.Status.PARSED:
            calculate_campaign_estimate(campaign)

        has_sufficient_balance = current_balance >= campaign.estimated_cost

        return render(
            request,
            self.template_name,
            {
                "campaign": campaign,
                "current_balance": current_balance,
                "has_sufficient_balance": has_sufficient_balance,
                "page_title": f"Review & Launch - {campaign.name}",
            },
        )

    def post(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)

        if campaign.status != Campaign.Status.PARSED:
            messages.error(request, f"Campaign cannot be launched from state: {campaign.get_status_display()}")
            return redirect("campaigns:campaign_review", campaign_id=campaign.id)

        action_type = request.POST.get("action_type", "now")  # 'now' or 'schedule'
        schedule_datetime_str = request.POST.get("scheduled_at", "").strip()

        # 1. Reserve funds in wallet atomically
        try:
            reserve_campaign_funds(campaign)
        except InsufficientBalanceError as e:
            messages.error(request, str(e))
            return redirect("campaigns:campaign_review", campaign_id=campaign.id)

        # 2. Dispatch now or schedule
        if action_type == "schedule" and schedule_datetime_str:
            dt = parse_datetime(schedule_datetime_str)
            if dt and timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())

            if dt and dt > timezone.now():
                campaign.scheduled_at = dt
                campaign.status = Campaign.Status.SCHEDULED
                campaign.save(update_fields=["scheduled_at", "status", "updated_at"])
                messages.success(request, f"Campaign scheduled for {dt.strftime('%Y-%m-%d %H:%M')}.")
                return redirect("campaigns:campaign_detail", campaign_id=campaign.id)

        # Launch immediately
        campaign.status = Campaign.Status.QUEUED
        campaign.save(update_fields=["status", "updated_at"])
        run_campaign_task.delay(str(campaign.id))
        messages.success(request, f"Campaign '{campaign.name}' queued and dispatched.")
        return redirect("campaigns:campaign_detail", campaign_id=campaign.id)


class CampaignDetailView(RoleRequiredMixin, View):
    """Step 4: Campaign report dashboard with live progress, metrics, and actions."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "campaigns/campaign_detail.html"

    def get(self, request, campaign_id):
        campaign = get_object_or_404(
            Campaign.objects.for_user(request.user).select_related("user", "sender_profile", "template"),
            id=campaign_id,
        )

        # Get recent contacts sample
        contacts_sample = (
            CampaignContact.objects.filter(campaign=campaign)
            .select_related("message")
            .order_by("id")[:20]
        )

        return render(
            request,
            self.template_name,
            {
                "campaign": campaign,
                "contacts_sample": contacts_sample,
                "page_title": f"Campaign Report - {campaign.name}",
            },
        )


class CampaignProgressAjaxView(RoleRequiredMixin, View):
    """HTMX polling partial returning real-time progress bar and metrics."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "campaigns/partials/campaign_progress.html"

    def get(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)
        return render(
            request,
            self.template_name,
            {"campaign": campaign},
        )


class CampaignActionView(RoleRequiredMixin, View):
    """Handle pause, resume, and cancel commands."""

    allowed_roles = ["admin", "reseller", "user"]

    def post(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)
        action = request.POST.get("action")

        if action == "pause":
            if pause_campaign(campaign):
                messages.warning(request, f"Campaign '{campaign.name}' paused.")
            else:
                messages.error(request, "Campaign cannot be paused in its current state.")

        elif action == "resume":
            if resume_campaign(campaign):
                messages.success(request, f"Campaign '{campaign.name}' resumed.")
            else:
                messages.error(request, "Campaign cannot be resumed in its current state.")

        elif action == "cancel":
            if cancel_campaign(campaign):
                messages.info(request, f"Campaign '{campaign.name}' cancelled. Unused reservation refunded.")
            else:
                messages.error(request, "Campaign cannot be cancelled.")

        return redirect("campaigns:campaign_detail", campaign_id=campaign.id)


class CampaignExportCSVView(RoleRequiredMixin, View):
    """Stream per-number delivery report as CSV attachment without memory load."""

    allowed_roles = ["admin", "reseller", "user"]

    def get(self, request, campaign_id):
        campaign = get_object_or_404(Campaign.objects.for_user(request.user), id=campaign_id)

        header = [
            "Recipient Number",
            "Contact Status",
            "Message ID",
            "OneXtel Carrier ID",
            "Message Status",
            "Dispatched At",
            "Delivered At",
            "Error / Reason",
        ]

        def format_row(c):
            m = c.message
            return [
                c.number,
                c.get_status_display(),
                str(m.id) if m else "",
                m.onextel_message_id if m and m.onextel_message_id else "",
                m.get_status_display() if m else "",
                m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m and m.created_at else "",
                m.delivered_at.strftime("%Y-%m-%d %H:%M:%S") if m and m.delivered_at else "",
                c.error_message or (m.failure_reason if m else ""),
            ]

        qs = CampaignContact.objects.filter(campaign=campaign).select_related("message").order_by("id")
        filename = f"campaign_{campaign.name.lower().replace(' ', '_')}_results.csv"

        return stream_csv_response(
            filename=filename,
            header=header,
            queryset_iterator=qs.iterator(chunk_size=1000),
            row_formatter=format_row,
        )
