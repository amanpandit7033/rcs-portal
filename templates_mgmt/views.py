"""Views for RCS template management, builder, live preview, and media uploads."""
import json
import logging
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from accounts.mixins import RoleRequiredMixin
from integrations.onextel.client import OneXtelClient, OneXtelClientError
from wallet.models import SenderProfile
from .forms import MediaUploadForm, TemplateFilterForm
from .models import MediaFile, Template
from .validators import validate_template_payload

logger = logging.getLogger(__name__)
User = get_user_model()


class TemplateListView(RoleRequiredMixin, ListView):
    """List of RCS templates scoped by user role with multi-field filtering."""

    model = Template
    template_name = "templates_mgmt/template_list.html"
    context_object_name = "templates_list"
    paginate_by = 15
    allowed_roles = ["admin", "reseller", "user"]

    def get_queryset(self):
        user = self.request.user
        qs = Template.objects.for_user(user).select_related("owner", "sender_profile").order_by("-created_at")

        self.filter_form = TemplateFilterForm(self.request.GET, user=user)
        if self.filter_form.is_valid():
            q = self.filter_form.cleaned_data.get("q")
            status = self.filter_form.cleaned_data.get("status")
            template_type = self.filter_form.cleaned_data.get("template_type")
            message_type = self.filter_form.cleaned_data.get("message_type")
            owner = self.filter_form.cleaned_data.get("owner")

            if q:
                qs = qs.filter(name__icontains=q)
            if status:
                qs = qs.filter(status=status)
            if template_type:
                qs = qs.filter(template_type=template_type)
            if message_type:
                qs = qs.filter(message_type=message_type)
            if owner and (user.is_admin or user.is_superuser):
                qs = qs.filter(owner=owner)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = getattr(self, "filter_form", TemplateFilterForm(self.request.GET, user=self.request.user))
        context["page_title"] = "RCS Templates Studio"
        return context


class TemplateCreateView(RoleRequiredMixin, View):
    """Builder view for creating and submitting rich RCS templates with live preview."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "templates_mgmt/template_builder.html"

    def get(self, request):
        user = request.user
        # Sender profiles available to user
        if user.is_admin or user.is_superuser:
            admin_profiles = SenderProfile.objects.filter(user=user, is_active=True)
            sender_profiles = admin_profiles if admin_profiles.exists() else SenderProfile.objects.filter(is_active=True)
        else:
            sender_profiles = SenderProfile.objects.filter(user=user, is_active=True)

        return render(
            request,
            self.template_name,
            {
                "sender_profiles": sender_profiles,
                "template_types": Template.TemplateType.choices,
                "message_types": Template.MessageType.choices,
                "page_title": "Create RCS Template",
            },
        )

    def post(self, request):
        user = request.user

        # Support both form data and raw JSON submissions
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body)
            except Exception:
                return JsonResponse({"error": "Invalid JSON body"}, status=400)
            name = data.get("name", "").strip()
            template_type = data.get("template_type")
            message_type = data.get("message_type")
            sender_profile_id = data.get("sender_profile_id")
            payload = data.get("payload", {})
        else:
            name = request.POST.get("name", "").strip()
            template_type = request.POST.get("template_type")
            message_type = request.POST.get("message_type")
            sender_profile_id = request.POST.get("sender_profile_id")
            raw_payload = request.POST.get("payload_json", "{}")
            try:
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            except Exception:
                messages.error(request, "Invalid JSON structure in template payload.")
                return redirect("templates_mgmt:template_create")

        # 1. Basic validation
        if not name:
            error_msg = "Template name is required."
            if request.content_type == "application/json":
                return JsonResponse({"error": error_msg}, status=400)
            messages.error(request, error_msg)
            return redirect("templates_mgmt:template_create")

        if Template.objects.filter(name=name).exists():
            error_msg = f"A template with name '{name}' already exists."
            if request.content_type == "application/json":
                return JsonResponse({"error": error_msg}, status=400)
            messages.error(request, error_msg)
            return redirect("templates_mgmt:template_create")

        # 2. Check SenderProfile
        if user.is_admin or user.is_superuser:
            sender_profile = get_object_or_404(SenderProfile, id=sender_profile_id, is_active=True)
        else:
            sender_profile = get_object_or_404(SenderProfile, id=sender_profile_id, user=user, is_active=True)

        # 3. Server-side Payload Validation
        try:
            validate_template_payload(template_type, payload)
        except ValidationError as e:
            error_msg = str(e.message if hasattr(e, "message") else e)
            if request.content_type == "application/json":
                return JsonResponse({"error": error_msg}, status=400)
            messages.error(request, f"Validation Error: {error_msg}")
            return redirect("templates_mgmt:template_create")

        # 4. Vendor Submission via OneXtel Client (OneXtel API Guide Release 1.0)
        data_payload = dict(payload)
        data_payload["name"] = name
        data_payload["type"] = template_type

        client = OneXtelClient()
        try:
            resp = client.create_template(
                data=data_payload,
                sender_profile_name=sender_profile.sender_profile_name,
                update_by=user.email,
                message_type=message_type,
            )
            raw_status = resp.get("status", "Pending")
        except OneXtelClientError as e:
            error_msg = f"OneXtel API Error: {e}"
            if request.content_type == "application/json":
                return JsonResponse({"error": error_msg}, status=502)
            messages.error(request, error_msg)
            return redirect("templates_mgmt:template_create")

        # 5. Persist Template Record
        template = Template.objects.create(
            owner=user,
            name=name,
            template_type=template_type,
            message_type=message_type,
            sender_profile=sender_profile,
            payload=data_payload,
            status=Template.Status.PENDING,
            onextel_status_raw=str(raw_status),
            last_synced_at=timezone.now(),
        )

        success_msg = f"Template '{template.name}' successfully submitted to carrier review."
        if request.content_type == "application/json":
            return JsonResponse({"status": "success", "id": template.id, "message": success_msg})

        messages.success(request, success_msg)
        return redirect("templates_mgmt:template_list")


class TemplateDetailView(RoleRequiredMixin, DetailView):
    """Inspect full template layout, approval status, and placeholders."""

    model = Template
    template_name = "templates_mgmt/template_detail.html"
    context_object_name = "template"
    allowed_roles = ["admin", "reseller", "user"]

    def get_queryset(self):
        return Template.objects.for_user(self.request.user).select_related("owner", "sender_profile")


class TemplateRefreshStatusView(RoleRequiredMixin, View):
    """Manually fetch latest status from OneXtel."""

    allowed_roles = ["admin", "reseller", "user"]

    def post(self, request, pk):
        template = get_object_or_404(Template.objects.for_user(request.user), pk=pk)
        client = OneXtelClient()

        try:
            resp = client.fetch_templates(
                sender_profile=template.sender_profile.sender_profile_name,
                template_name=template.name,
                message_type=template.message_type,
            )
            # OneXtel returns templates list under 'data' or 'templates'
            templates_list = resp.get("data") or resp.get("templates") or []
            matched = next((t for t in templates_list if (t.get("name") or t.get("templateName")) == template.name), None)

            if matched:
                raw = str(matched.get("status", "")).upper()
                template.onextel_status_raw = raw
                if "APPROV" in raw or raw == "ACTIVE":
                    template.status = Template.Status.APPROVED
                elif "REJECT" in raw:
                    template.status = Template.Status.REJECTED
                template.last_synced_at = timezone.now()
                template.save(update_fields=["status", "onextel_status_raw", "last_synced_at", "updated_at"])
                messages.success(request, f"Status updated: {template.get_status_display()} ({raw}).")
            else:
                template.last_synced_at = timezone.now()
                template.save(update_fields=["last_synced_at"])
                messages.info(request, "Template status checked. Still pending carrier approval.")

        except Exception as e:
            messages.error(request, f"Failed to refresh status: {e}")

        return redirect("templates_mgmt:template_list")


class MediaUploadAjaxView(RoleRequiredMixin, View):
    """Handles AJAX file uploads and returns public media URL."""

    allowed_roles = ["admin", "reseller", "user"]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return JsonResponse({"error": "No file uploaded"}, status=400)

        media = MediaFile.objects.create(owner=request.user, file=uploaded_file)
        # Generate full URL
        public_url = request.build_absolute_uri(media.file.url)
        media.public_url = public_url
        media.save(update_fields=["public_url"])

        return JsonResponse({
            "status": "success",
            "url": public_url,
            "filename": uploaded_file.name,
            "id": media.id,
        })
