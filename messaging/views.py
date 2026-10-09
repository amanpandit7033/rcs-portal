"""Views for viewing RCS delivery logs, message details, and rate calculation."""
import logging
from django.db import models
from django.http import JsonResponse
from django.views import View
from django.views.generic import DetailView, ListView

from accounts.mixins import RoleRequiredMixin
from .forms import MessageFilterForm
from .models import Message
from .services import get_user_rate_for_type

logger = logging.getLogger(__name__)


class MessageLogView(RoleRequiredMixin, ListView):
    """Paginated list of dispatched RCS messages scoped by user role."""

    model = Message
    template_name = "messaging/message_logs.html"
    context_object_name = "messages_list"
    paginate_by = 20
    allowed_roles = ["admin", "reseller", "user"]

    def get_queryset(self):
        user = self.request.user
        qs = Message.objects.for_user(user).select_related(
            "user", "sender_profile", "template"
        ).order_by("-created_at")

        self.filter_form = MessageFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            q = self.filter_form.cleaned_data.get("q")
            status = self.filter_form.cleaned_data.get("status")
            message_type = self.filter_form.cleaned_data.get("message_type")
            date = self.filter_form.cleaned_data.get("date")

            if q:
                qs = qs.filter(
                    models.Q(recipient__icontains=q)
                    | models.Q(id__icontains=q)
                    | models.Q(onextel_message_id__icontains=q)
                )
            if status:
                qs = qs.filter(status=status)
            if message_type:
                qs = qs.filter(message_type=message_type)
            if date:
                qs = qs.filter(created_at__date=date)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = getattr(self, "filter_form", MessageFilterForm(self.request.GET))
        context["page_title"] = "RCS Message Logs"
        return context


class MessageDetailView(RoleRequiredMixin, DetailView):
    """Inspect full message payload, custom params, and delivery timestamps."""

    model = Message
    template_name = "messaging/message_detail.html"
    context_object_name = "message_obj"
    allowed_roles = ["admin", "reseller", "user"]

    def get_queryset(self):
        return Message.objects.for_user(self.request.user).select_related(
            "user", "sender_profile", "template"
        )


class UserRatePreviewAjaxView(RoleRequiredMixin, View):
    """Return rate for a given message type to support live UI cost calculation."""

    allowed_roles = ["admin", "reseller", "user"]

    def get(self, request):
        message_type = request.GET.get("message_type", Message.MessageType.PROMOTIONAL)
        try:
            rate = get_user_rate_for_type(request.user, message_type)
            return JsonResponse({"rate": str(rate), "status": "success"})
        except Exception as e:
            return JsonResponse({"error": str(e), "rate": "0.0000"}, status=404)
