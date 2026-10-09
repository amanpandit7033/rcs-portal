"""Views for Client Bot Onboarding and Admin Management."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.mixins import AdminRequiredMixin
from .forms import OnboardingApplicationForm, OnboardingStatusUpdateForm
from .models import OnboardingApplication
from .utils import generate_onboarding_excel, generate_onboarding_zip


# ---------------------------------------------------------------------------
# Client Views (Authenticated Users & Resellers)
# ---------------------------------------------------------------------------

class ClientOnboardingListView(LoginRequiredMixin, ListView):
    """Client view displaying their submitted onboarding requests."""
    model = OnboardingApplication
    template_name = "onboarding/client_list.html"
    context_object_name = "applications"
    paginate_by = 15

    def get_queryset(self):
        return OnboardingApplication.objects.filter(user=self.request.user).order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "RCS Bot Onboarding"
        return context


class ClientOnboardingCreateView(LoginRequiredMixin, View):
    """Client form replicating the operator Google Form to submit bot onboarding details."""
    template_name = "onboarding/client_form.html"

    def get(self, request, *args, **kwargs):
        form = OnboardingApplicationForm(user=request.user)
        return render(request, self.template_name, {
            "form": form,
            "page_title": "Submit Bot Onboarding",
        })

    def post(self, request, *args, **kwargs):
        form = OnboardingApplicationForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            application = form.save()
            messages.success(
                request,
                f"Bot onboarding application for '{application.bot_name}' submitted successfully! Our team will review and forward it to the telecom operator."
            )
            return redirect("onboarding:client_detail", pk=application.pk)
        else:
            messages.error(request, "Please correct the highlighted errors before submitting.")
            return render(request, self.template_name, {
                "form": form,
                "page_title": "Submit Bot Onboarding",
            })


class ClientOnboardingDetailView(LoginRequiredMixin, DetailView):
    """Client view to inspect their submitted onboarding dossier."""
    model = OnboardingApplication
    template_name = "onboarding/client_detail.html"
    context_object_name = "application"

    def get_queryset(self):
        # Client can only view their own applications (or admin can view any)
        if self.request.user.is_admin:
            return OnboardingApplication.objects.all()
        return OnboardingApplication.objects.filter(user=self.request.user)


# ---------------------------------------------------------------------------
# Superadmin Management Views
# ---------------------------------------------------------------------------

class AdminOnboardingListView(AdminRequiredMixin, ListView):
    """Superadmin view listing all client onboarding applications."""
    model = OnboardingApplication
    template_name = "onboarding/admin_list.html"
    context_object_name = "applications"
    paginate_by = 20

    def get_queryset(self):
        qs = OnboardingApplication.objects.select_related("user").order_by("-created_at")
        status = self.request.GET.get("status")
        search = self.request.GET.get("q")

        if status:
            qs = qs.filter(status=status)
        if search:
            qs = qs.filter(
                Q(brand_name__icontains=search)
                | Q(bot_name__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__company_name__icontains=search)
                | Q(brand_spoc_name__icontains=search)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_qs = OnboardingApplication.objects.all()
        context["counts"] = {
            "all": base_qs.count(),
            "submitted": base_qs.filter(status=OnboardingApplication.Status.SUBMITTED).count(),
            "forwarded": base_qs.filter(status=OnboardingApplication.Status.FORWARDED_TO_OPERATOR).count(),
            "approved": base_qs.filter(status=OnboardingApplication.Status.APPROVED).count(),
            "rejected": base_qs.filter(status=OnboardingApplication.Status.REJECTED).count(),
        }
        context["current_status"] = self.request.GET.get("status", "")
        context["search_query"] = self.request.GET.get("q", "")
        context["page_title"] = "Operator Onboarding Management"
        return context


class AdminOnboardingDetailView(AdminRequiredMixin, DetailView):
    """Superadmin detail view with operator status update and media inspection."""
    model = OnboardingApplication
    template_name = "onboarding/admin_detail.html"
    context_object_name = "application"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_form"] = OnboardingStatusUpdateForm(instance=self.object)
        context["page_title"] = f"Onboarding Application #{self.object.id}: {self.object.brand_name}"
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        status_form = OnboardingStatusUpdateForm(request.POST, instance=self.object)
        if status_form.is_valid():
            status_form.save()
            messages.success(request, f"Application #{self.object.id} status updated to '{self.object.get_status_display()}'.")
            return redirect("onboarding:admin_detail", pk=self.object.pk)
        else:
            messages.error(request, "Failed to update application status.")
            return render(request, self.template_name, {
                "application": self.object,
                "status_form": status_form,
                "page_title": f"Onboarding Application #{self.object.id}: {self.object.brand_name}",
            })


# ---------------------------------------------------------------------------
# Download / Export Views (ZIP & Excel)
# ---------------------------------------------------------------------------

class DownloadApplicationZipView(LoginRequiredMixin, View):
    """Download single application dossier with all images and specs as a ZIP."""

    def get(self, request, pk):
        app = get_object_or_404(OnboardingApplication, pk=pk)
        if not (request.user.is_admin or app.user == request.user):
            raise PermissionDenied("You do not have permission to download this application.")

        zip_buffer = generate_onboarding_zip(app)
        filename = f"rcs_onboarding_{app.bot_name.lower().replace(' ', '_')}_{app.id}.zip"

        response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class DownloadAllApplicationsExcelView(AdminRequiredMixin, View):
    """Export applications as Excel file for telecom carrier forwarding."""

    def get(self, request):
        status = request.GET.get("status")
        qs = OnboardingApplication.objects.select_related("user").order_by("-created_at")
        if status:
            qs = qs.filter(status=status)

        excel_buffer = generate_onboarding_excel(qs)
        filename = "rcs_carrier_onboarding_applications.xlsx"

        response = HttpResponse(
            excel_buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
