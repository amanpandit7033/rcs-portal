import sys
import django
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from .forms import (
    ClientCreateForm,
    ClientEditForm,
    LoginForm,
    PasswordResetForm,
    ProfileSettingsForm,
    SetPasswordForm,
    UserCreateForm,
    UserEditForm,
    UserKYCForm,
    UserPasswordChangeForm,
    WhiteLabelForm,
    WhitelistNumberForm,
)
from .models import WhiteLabel, WhitelistNumber, UserKYC

from .mixins import AdminRequiredMixin, ResellerRequiredMixin, UserRequiredMixin
from wallet.models import Wallet
from wallet.utils import format_inr

User = get_user_model()


class UserLoginView(LoginView):
    """Tailwind-styled Login view with Axes protection."""
    template_name = "accounts/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("accounts:dashboard_redirect")


class UserLogoutView(LogoutView):
    """Logout view redirecting to login."""
    next_page = reverse_lazy("accounts:login")


class DashboardRedirectView(LoginRequiredMixin, View):
    """Inspects user role and redirects to the appropriate dashboard."""

    def get(self, request, *args, **kwargs):
        user = request.user
        if user.is_superuser or user.role in [User.Role.SUPERADMIN, "admin"]:
            return redirect("accounts:admin_dashboard")
        elif user.role == User.Role.RESELLER:
            return redirect("accounts:reseller_dashboard")
        else:
            return redirect("accounts:user_dashboard")


# ---------------------------------------------------------------------------
# Role Dashboards (Showing Counts Only for Now)
# ---------------------------------------------------------------------------

class AdminDashboardView(AdminRequiredMixin, TemplateView):
    """Admin overview dashboard showing system-wide counts and real operational metrics."""
    template_name = "dashboard/admin_dashboard.html"

    def get_context_data(self, **kwargs):
        from datetime import timedelta
        from django.utils import timezone
        from django.db.models import Count, Q, Sum
        from django.db.models.functions import TruncDate
        from messaging.models import Message
        from campaigns.models import Campaign
        from templates_mgmt.models import Template
        from onboarding.models import OnboardingApplication

        context = super().get_context_data(**kwargs)
        context["page_title"] = "Admin Dashboard"

        now = timezone.now()
        today_date = timezone.localdate()

        # Users and entities
        context["total_users"] = User.objects.count()
        context["total_resellers"] = User.objects.filter(role=User.Role.RESELLER).count()
        context["total_clients"] = User.objects.filter(role=User.Role.USER).count()

        # Total platform funds in all wallets
        total_balance = Wallet.objects.aggregate(total=Sum("balance"))["total"] or Decimal("0.0000")
        context["total_platform_balance"] = format_inr(total_balance)

        # Messaging platform metrics
        all_msgs = Message.objects.all()
        msg_agg = all_msgs.aggregate(
            total_sent=Count("id", filter=~Q(status=Message.Status.QUEUED)),
            total_delivered=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
            total_failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            total_read=Count("id", filter=Q(status=Message.Status.READ)),
            today_sent=Count("id", filter=Q(created_at__date=today_date, status__in=[Message.Status.DELIVERED, Message.Status.READ, Message.Status.SENT, Message.Status.FAILED])),
        )
        context["total_messages_sent"] = msg_agg["total_sent"] or 0
        context["total_messages_delivered"] = msg_agg["total_delivered"] or 0
        context["total_messages_failed"] = msg_agg["total_failed"] or 0
        context["total_messages_read"] = msg_agg["total_read"] or 0
        context["today_messages_sent"] = msg_agg["today_sent"] or 0

        # Campaigns & Templates
        context["total_campaigns"] = Campaign.objects.count()
        context["active_campaigns"] = Campaign.objects.filter(status__in=[Campaign.Status.RUNNING, Campaign.Status.QUEUED]).count()
        context["total_templates"] = Template.objects.count()
        context["approved_templates"] = Template.objects.filter(status="approved").count()
        context["total_onboardings"] = OnboardingApplication.objects.count()
        context["pending_onboardings"] = OnboardingApplication.objects.filter(status=OnboardingApplication.Status.SUBMITTED).count()

        # Traffic Chart: Last 7 days platform messages
        chart_since = now - timedelta(days=7)
        daily_records = (
            all_msgs.filter(created_at__gte=chart_since)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                sent=Count("id", filter=~Q(status=Message.Status.QUEUED)),
                delivered=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
                failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            )
            .order_by("day")
        )
        daily_map = {r["day"]: r for r in daily_records}
        admin_chart_labels = []
        admin_chart_sent = []
        admin_chart_delivered = []
        admin_chart_failed = []
        for offset in range(6, -1, -1):
            d = today_date - timedelta(days=offset)
            rec = daily_map.get(d, {})
            admin_chart_labels.append(d.strftime("%a"))
            admin_chart_sent.append(rec.get("sent", 0))
            admin_chart_delivered.append(rec.get("delivered", 0))
            admin_chart_failed.append(rec.get("failed", 0))

        context["admin_chart_labels"] = admin_chart_labels
        context["admin_chart_sent"] = admin_chart_sent
        context["admin_chart_delivered"] = admin_chart_delivered
        context["admin_chart_failed"] = admin_chart_failed

        # Message Type Distribution (Donut Chart)
        promo_count = all_msgs.filter(message_type=Message.MessageType.PROMOTIONAL).count()
        trans_count = all_msgs.filter(message_type=Message.MessageType.TRANSACTIONAL).count()
        otp_count = all_msgs.filter(message_type=Message.MessageType.OTP).count()
        context["msg_type_promo"] = promo_count
        context["msg_type_trans"] = trans_count
        context["msg_type_otp"] = otp_count
        context["donut_labels"] = ["Promotional", "Transactional", "OTP"]
        context["donut_data"] = [promo_count, trans_count, otp_count]
        context["total_typed_msgs"] = promo_count + trans_count + otp_count

        # Recent Platform Campaigns
        context["recent_campaigns"] = Campaign.objects.select_related("user").order_by("-created_at")[:5]

        # Recent Onboarding Applications
        context["recent_onboardings"] = OnboardingApplication.objects.select_related("user").order_by("-created_at")[:5]

        # Top Clients by message volume
        top_clients = (
            User.objects.filter(role=User.Role.USER)
            .annotate(msg_count=Count("rcs_messages"))
            .order_by("-msg_count")[:5]
        )
        context["top_clients"] = top_clients

        return context


class ResellerDashboardView(ResellerRequiredMixin, TemplateView):
    """Reseller overview dashboard showing managed counts and activity."""
    template_name = "dashboard/reseller_dashboard.html"

    def get_context_data(self, **kwargs):
        from messaging.models import Message
        from django.db.models import Q
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Reseller Dashboard"
        reseller = self.request.user
        clients = User.objects.filter(parent_reseller=reseller)
        context["clients_count"] = clients.count()
        context["recent_clients"] = clients[:5]

        # Reseller wallet balance
        reseller_wallet = getattr(reseller, "wallet", None)
        context["reseller_balance"] = format_inr(reseller_wallet.balance) if reseller_wallet else "₹ 0.00"

        # Client messages count
        client_ids = clients.values_list("id", flat=True)
        reseller_msgs = Message.objects.filter(user_id__in=client_ids)
        reseller_sent = reseller_msgs.filter(~Q(status=Message.Status.QUEUED)).count()
        context["total_client_messages"] = reseller_sent

        return context


class UserDashboardView(UserRequiredMixin, TemplateView):
    """Standard user/client overview dashboard."""
    template_name = "dashboard/user_dashboard.html"

    def get_context_data(self, **kwargs):
        from datetime import timedelta
        from django.utils import timezone
        from django.db.models import Count, Q
        from django.db.models.functions import TruncDate
        from messaging.models import Message
        from templates_mgmt.models import Template

        context = super().get_context_data(**kwargs)
        context["page_title"] = "User Dashboard"
        user = self.request.user
        user_wallet = getattr(user, "wallet", None)
        context["client_balance"] = format_inr(user_wallet.balance) if user_wallet else "₹ 0.00"

        now = timezone.now()
        today_date = timezone.localdate()

        def get_stats_for_qs(qs):
            agg = qs.aggregate(
                total=Count("id"),
                sent=Count("id", filter=~Q(status=Message.Status.QUEUED)),
                delivered=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
                failed=Count("id", filter=Q(status=Message.Status.FAILED)),
                read=Count("id", filter=Q(status=Message.Status.READ)),
            )
            return {
                "total": agg["total"] or 0,
                "sent": agg["sent"] or 0,
                "delivered": agg["delivered"] or 0,
                "failed": agg["failed"] or 0,
                "read": agg["read"] or 0,
            }

        user_msgs = Message.objects.filter(user=user)
        stats_today = get_stats_for_qs(user_msgs.filter(created_at__date=today_date))
        stats_7d = get_stats_for_qs(user_msgs.filter(created_at__gte=now - timedelta(days=7)))
        stats_30d = get_stats_for_qs(user_msgs.filter(created_at__gte=now - timedelta(days=30)))

        period = self.request.GET.get("period", "7d")
        if period == "today":
            active_stats = stats_today
            days_count = 1
        elif period == "30d":
            active_stats = stats_30d
            days_count = 30
        else:
            period = "7d"
            active_stats = stats_7d
            days_count = 7

        # Daily chart data for selected period
        chart_since = now - timedelta(days=days_count)
        daily_records = (
            user_msgs.filter(created_at__gte=chart_since)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                sent=Count("id", filter=~Q(status=Message.Status.QUEUED)),
                delivered=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
                failed=Count("id", filter=Q(status=Message.Status.FAILED)),
                read=Count("id", filter=Q(status=Message.Status.READ)),
            )
            .order_by("day")
        )

        # Generate complete date range so every day shows real counts (even 0)
        daily_map = {r["day"]: r for r in daily_records}
        chart_labels = []
        chart_sent = []
        chart_delivered = []
        chart_failed = []
        chart_read = []

        if period == "today":
            d = today_date
            rec = daily_map.get(d, {})
            chart_labels = [d.strftime("%b %d")]
            chart_sent = [rec.get("sent", 0)]
            chart_delivered = [rec.get("delivered", 0)]
            chart_failed = [rec.get("failed", 0)]
            chart_read = [rec.get("read", 0)]
        else:
            for offset in range(days_count - 1, -1, -1):
                d = today_date - timedelta(days=offset)
                rec = daily_map.get(d, {})
                chart_labels.append(d.strftime("%a") if days_count == 7 else d.strftime("%b %d"))
                chart_sent.append(rec.get("sent", 0))
                chart_delivered.append(rec.get("delivered", 0))
                chart_failed.append(rec.get("failed", 0))
                chart_read.append(rec.get("read", 0))

        # Total templates approved
        context["templates_count"] = Template.objects.filter(owner=user, status="approved").count()
        context["stats_today"] = stats_today
        context["stats_7d"] = stats_7d
        context["stats_30d"] = stats_30d
        context["active_stats"] = active_stats
        context["selected_period"] = period
        context["chart_labels"] = chart_labels
        context["chart_sent"] = chart_sent
        context["chart_delivered"] = chart_delivered
        context["chart_failed"] = chart_failed
        context["chart_read"] = chart_read

        # Compute delivery rate for active period
        total_for_rate = active_stats["sent"]
        if total_for_rate > 0:
            context["delivery_rate"] = f"{(active_stats['delivered'] / total_for_rate * 100):.1f}%"
        else:
            context["delivery_rate"] = "0.0%"

        return context



# ---------------------------------------------------------------------------
# Admin User & Reseller Management Screens
# ---------------------------------------------------------------------------

class UserListView(AdminRequiredMixin, ListView):
    """Admin view listing all users, resellers, and admins with search/role filters and modal creation."""
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users_list"
    paginate_by = 20

    def get_queryset(self):
        qs = User.objects.select_related("parent_reseller", "wallet").prefetch_related("rate_plans", "sender_profiles").order_by("-date_joined")
        role = self.request.GET.get("role")
        search = self.request.GET.get("q")

        if role:
            qs = qs.filter(role=role)
        if search:
            qs = qs.filter(
                Q(email__icontains=search)
                | Q(company_name__icontains=search)
                | Q(phone__icontains=search)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_role"] = self.request.GET.get("role", "")
        context["search_query"] = self.request.GET.get("q", "")
        context["page_title"] = "Users & Resellers Management"
        if "form" not in context:
            context["form"] = UserCreateForm()
        context["open_modal"] = bool(self.request.GET.get("open_create"))
        context["open_edit_id"] = self.request.GET.get("open_edit", "")
        context["open_rates_id"] = self.request.GET.get("open_rates", "")
        context["open_senders_id"] = self.request.GET.get("open_senders", "")

        import json
        users_json = {}
        for u in context["users_list"]:
            users_json[str(u.id)] = {
                "id": u.id,
                "username": u.username,
                "email": u.email or "",
                "company_name": u.company_name or "",
                "first_name": u.first_name or "",
                "last_name": u.last_name or "",
                "phone": u.phone or "",
                "state": u.state or "",
                "GSTIN": u.GSTIN or "",
                "role": u.role,
                "is_active": u.is_active,
                "can_use_low_price": u.can_use_low_price,
                "is_low_price_applied": u.is_low_price_applied,
                "low_price_start_from": u.low_price_start_from,
                "low_price_percentage": str(u.low_price_percentage),
                "rates": u.get_rates_dict(),
                "senders": u.get_sender_profiles_list(),
            }
        context["users_json"] = json.dumps(users_json)
        return context

    def post(self, request, *args, **kwargs):
        form = UserCreateForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            messages.success(request, f"Account {new_user.username or new_user.email} created successfully.")
            return redirect("accounts:user_list")

        self.object_list = self.get_queryset()
        context = self.get_context_data(form=form)
        context["open_modal"] = True
        return self.render_to_response(context)


class UserCreateView(AdminRequiredMixin, CreateView):
    """Admin view redirecting to modal creation on UserListView or handling API POST."""
    model = User
    form_class = UserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def get(self, request, *args, **kwargs):
        return redirect(f"{reverse('accounts:user_list')}?open_create=1")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"Account {self.object.username or self.object.email} created successfully.")
        return response

    def form_invalid(self, form):
        messages.error(self.request, "Please correct the errors in the form.")
        return redirect(f"{reverse('accounts:user_list')}?open_create=1")


class UserEditView(AdminRequiredMixin, UpdateView):
    """Admin view to edit user/reseller settings."""
    model = User
    form_class = UserEditForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def get(self, request, *args, **kwargs):
        return redirect(f"{reverse('accounts:user_list')}?open_edit={self.get_object().id}")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"User {self.object.email} updated successfully.")
        return response

    def form_invalid(self, form):
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(self.request, f"{field.title()}: {err}")
        return redirect(f"{reverse('accounts:user_list')}?open_edit={self.get_object().id}")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = f"Edit Account: {self.object.email}"
        context["submit_label"] = "Update Account"
        return context


class ToggleUserActiveView(AdminRequiredMixin, View):
    """Admin toggle to suspend or activate a user account."""

    def post(self, request, user_id):
        target = get_object_or_404(User, id=user_id)
        if target == request.user:
            messages.error(request, "You cannot suspend your own administrative account.")
            return redirect("accounts:user_list")

        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        status_str = "Activated" if target.is_active else "Suspended"
        messages.success(request, f"Account {target.email} is now {status_str}.")
        return redirect("accounts:user_list")


# ---------------------------------------------------------------------------
# Reseller Client Management Screens
# ---------------------------------------------------------------------------

class ClientListView(ResellerRequiredMixin, ListView):
    """Reseller view listing only their own onboarded clients with modal creation, editing, and rates."""
    model = User
    template_name = "accounts/client_list.html"
    context_object_name = "clients_list"
    paginate_by = 20

    def get_queryset(self):
        reseller = self.request.user
        qs = User.objects.filter(parent_reseller=reseller).select_related("wallet").prefetch_related("rate_plans").order_by("-date_joined")
        search = self.request.GET.get("q")
        if search:
            qs = qs.filter(
                Q(email__icontains=search)
                | Q(company_name__icontains=search)
                | Q(phone__icontains=search)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.request.GET.get("q", "")
        context["page_title"] = "Client Management"
        if "form" not in context:
            context["form"] = ClientCreateForm(reseller=self.request.user)
        context["open_create"] = bool(self.request.GET.get("open_create"))
        context["open_edit_id"] = self.request.GET.get("open_edit", "")
        context["open_rates_id"] = self.request.GET.get("open_rates", "")
        context["reseller_rates"] = self.request.user.get_rates_dict()

        import json
        clients_json = {}
        for c in context["clients_list"]:
            clients_json[str(c.id)] = {
                "id": c.id,
                "username": c.username,
                "email": c.email or "",
                "company_name": c.company_name or "",
                "first_name": c.first_name or "",
                "last_name": c.last_name or "",
                "phone": c.phone or "",
                "state": c.state or "",
                "GSTIN": c.GSTIN or "",
                "role": c.role,
                "is_active": c.is_active,
                "can_use_low_price": c.can_use_low_price,
                "is_low_price_applied": c.is_low_price_applied,
                "low_price_start_from": c.low_price_start_from,
                "low_price_percentage": str(c.low_price_percentage),
                "rates": c.get_rates_dict(),
            }
        context["clients_json"] = json.dumps(clients_json)
        return context

    def post(self, request, *args, **kwargs):
        form = ClientCreateForm(request.POST, reseller=request.user)
        if form.is_valid():
            client = form.save()
            messages.success(request, f"Client {client.email or client.username} onboarded successfully.")
            return redirect("accounts:client_list")

        self.object_list = self.get_queryset()
        context = self.get_context_data(form=form)
        context["open_create"] = True
        return self.render_to_response(context)


class ClientCreateView(ResellerRequiredMixin, View):
    """Reseller view redirecting to modal creation on ClientListView or handling POST."""

    def get(self, request, *args, **kwargs):
        return redirect(f"{reverse('accounts:client_list')}?open_create=1")

    def post(self, request, *args, **kwargs):
        form = ClientCreateForm(request.POST, reseller=request.user)
        if form.is_valid():
            client = form.save()
            messages.success(request, f"Client {client.email or client.username} onboarded successfully.")
            return redirect("accounts:client_list")
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"{field.title()}: {err}")
        return redirect(f"{reverse('accounts:client_list')}?open_create=1")


class ClientEditView(ResellerRequiredMixin, View):
    """Reseller view redirecting to modal edit on ClientListView or handling POST."""

    def get(self, request, pk, *args, **kwargs):
        return redirect(f"{reverse('accounts:client_list')}?open_edit={pk}")

    def post(self, request, pk, *args, **kwargs):
        client = get_object_or_404(User, pk=pk, parent_reseller=request.user)
        form = ClientEditForm(request.POST, instance=client)
        if form.is_valid():
            form.save()
            messages.success(request, f"Client {client.email or client.username} updated successfully.")
            return redirect("accounts:client_list")
        for field, errors in form.errors.items():
            for err in errors:
                messages.error(request, f"{field.title()}: {err}")
        return redirect(f"{reverse('accounts:client_list')}?open_edit={pk}")


class ToggleClientActiveView(ResellerRequiredMixin, View):
    """Reseller toggle to suspend or activate their direct client."""

    def post(self, request, client_id):
        client = get_object_or_404(User, id=client_id, parent_reseller=request.user)
        client.is_active = not client.is_active
        client.save(update_fields=["is_active"])
        status_str = "Activated" if client.is_active else "Suspended"
        messages.success(request, f"Client {client.email} is now {status_str}.")
        return redirect("accounts:client_list")


# ---------------------------------------------------------------------------
# "Login as Client" Impersonation Flow
# ---------------------------------------------------------------------------

class ImpersonateClientView(ResellerRequiredMixin, View):
    """Allows a Reseller to impersonate one of their direct clients."""

    def post(self, request, client_id):
        client = get_object_or_404(User, id=client_id, parent_reseller=request.user)
        reseller_id = request.user.id

        # Log in as client
        auth_login(request, client, backend="django.contrib.auth.backends.ModelBackend")

        # Store reseller's ID in the new authenticated session
        request.session["_impersonator_user_id"] = reseller_id
        request.session.modified = True

        messages.warning(
            request,
            f"You are now logged in as {client.company_name or client.email} in Impersonation Mode."
        )
        return redirect("accounts:user_dashboard")


class StopImpersonationView(LoginRequiredMixin, View):
    """Terminates impersonation session and returns to original Reseller account."""

    def post(self, request):
        impersonator_id = request.session.get("_impersonator_user_id")
        if not impersonator_id:
            return redirect("accounts:dashboard_redirect")

        reseller = get_object_or_404(User, id=impersonator_id)

        # Restore original reseller session
        auth_login(request, reseller, backend="django.contrib.auth.backends.ModelBackend")
        if "_impersonator_user_id" in request.session:
            del request.session["_impersonator_user_id"]
        request.session.modified = True

        messages.success(request, "Returned safely to your Reseller account.")
        return redirect("accounts:reseller_dashboard")


class UserProfileView(LoginRequiredMixin, View):
    """User Profile & Account Details View for all authenticated roles."""
    template_name = "accounts/user_profile.html"

    def get(self, request, *args, **kwargs):
        profile_form = ProfileSettingsForm(instance=request.user)
        password_form = UserPasswordChangeForm(user=request.user)
        active_tab = request.GET.get("tab", "overview")

        context = self.get_context_data(
            profile_form=profile_form,
            password_form=password_form,
            active_tab=active_tab,
        )
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "update_profile")
        profile_form = ProfileSettingsForm(instance=request.user)
        password_form = UserPasswordChangeForm(user=request.user)
        active_tab = "overview"

        if action == "update_profile":
            active_tab = "profile"
            profile_form = ProfileSettingsForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Your profile details have been updated successfully.")
                return redirect(f"{reverse('accounts:user_profile')}?tab=profile")
            else:
                messages.error(request, "Please review the errors in the profile form.")

        elif action == "change_password":
            active_tab = "security"
            password_form = UserPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Your password has been changed successfully.")
                return redirect(f"{reverse('accounts:user_profile')}?tab=security")
            else:
                messages.error(request, "Please review the password requirements below.")

        context = self.get_context_data(
            profile_form=profile_form,
            password_form=password_form,
            active_tab=active_tab,
        )
        return render(request, self.template_name, context)

    def get_context_data(self, profile_form, password_form, active_tab="overview"):
        user = self.request.user
        rates = user.get_rates_dict() if hasattr(user, "get_rates_dict") else {}
        wallet_obj = Wallet.objects.filter(user=user).first()
        raw_balance = wallet_obj.balance if wallet_obj else Decimal("0.0000")
        formatted_balance = format_inr(raw_balance)

        return {
            "page_title": "My Profile",
            "active_tab": active_tab,
            "profile_form": profile_form,
            "password_form": password_form,
            "rates": rates,
            "wallet_balance": formatted_balance,
            "raw_balance": raw_balance,
        }


class SystemSettingsView(AdminRequiredMixin, View):
    """System & Account Settings View. Restricted to Superadmins."""
    template_name = "accounts/system_settings.html"

    def get(self, request, *args, **kwargs):
        profile_form = ProfileSettingsForm(instance=request.user)
        password_form = UserPasswordChangeForm(user=request.user)
        active_tab = request.GET.get("tab", "profile")

        context = self.get_context_data(
            profile_form=profile_form,
            password_form=password_form,
            active_tab=active_tab,
        )
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "update_profile")
        profile_form = ProfileSettingsForm(instance=request.user)
        password_form = UserPasswordChangeForm(user=request.user)
        active_tab = "profile"

        if action == "update_profile":
            active_tab = "profile"
            profile_form = ProfileSettingsForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Organization profile updated successfully.")
                return redirect(f"{reverse('accounts:system_settings')}?tab=profile")
            else:
                messages.error(request, "Please review the errors in the profile form.")

        elif action == "change_password":
            active_tab = "security"
            password_form = UserPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Your password has been changed successfully.")
                return redirect(f"{reverse('accounts:system_settings')}?tab=security")
            else:
                messages.error(request, "Please review the password requirements below.")

        context = self.get_context_data(
            profile_form=profile_form,
            password_form=password_form,
            active_tab=active_tab,
        )
        return render(request, self.template_name, context)

    def get_context_data(self, profile_form, password_form, active_tab="profile"):
        gateway_mock = getattr(settings, "ONEXTEL_MOCK", True)
        gateway_url = getattr(settings, "ONEXTEL_BASE_URL", "https://api.onexaura.com")
        gateway_tps = getattr(settings, "ONEXTEL_MAX_TPS", 100)
        axes_limit = getattr(settings, "AXES_FAILURE_LIMIT", 5)
        axes_cooloff = getattr(settings, "AXES_COOLOFF_TIME", 1)

        return {
            "page_title": "System Settings",
            "active_tab": active_tab,
            "profile_form": profile_form,
            "password_form": password_form,
            "gateway_info": {
                "mock_mode": gateway_mock,
                "base_url": gateway_url,
                "max_tps": gateway_tps,
                "default_ttl": "86,400s (24 Hours)",
                "low_balance_threshold": "₹500.00",
                "vendor": "OneXtel RCS Carrier Network",
            },
            "security_info": {
                "axes_limit": axes_limit,
                "axes_cooloff": f"{axes_cooloff} hour(s)" if axes_cooloff > 1 else f"{axes_cooloff} minute",
                "hashing_algo": "PBKDF2 SHA-256",
                "session_engine": "Signed Cookie Database Session",
            },
            "diagnostics": {
                "python_version": sys.version.split()[0],
                "django_version": django.get_version(),
                "timezone": getattr(settings, "TIME_ZONE", "Asia/Kolkata"),
                "currency": "INR (₹)",
                "rate_limit": "120 requests/minute",
            },
        }


class ComingSoonView(LoginRequiredMixin, TemplateView):
    """Placeholder view for features under development."""
    template_name = "common/coming_soon.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        feature_name = self.request.GET.get("feature", "Feature")
        context["feature_name"] = feature_name
        context["page_title"] = f"{feature_name} - Coming Soon"
        return context


# ---------------------------------------------------------------------------
# Password Reset Views
# ---------------------------------------------------------------------------

class UserPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset.html"
    form_class = PasswordResetForm
    email_template_name = "accounts/password_reset_email.html"
    success_url = reverse_lazy("accounts:password_reset_done")


class UserPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class UserPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class UserPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---------------------------------------------------------------------------
# White Label Management Views (Admin Only)
# ---------------------------------------------------------------------------

class WhiteLabelListView(AdminRequiredMixin, ListView):
    """Admin-only list of white-label domain configurations."""
    model = WhiteLabel
    template_name = "accounts/whitelabel_list.html"
    context_object_name = "whitelabels"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "White Label Management"
        return context


class WhiteLabelCreateView(AdminRequiredMixin, CreateView):
    """Admin-only creation of white-label configuration."""
    model = WhiteLabel
    form_class = WhiteLabelForm
    template_name = "accounts/whitelabel_form.html"
    success_url = reverse_lazy("accounts:whitelabel_list")

    def form_valid(self, form):
        messages.success(self.request, f"White-label branding for '{form.instance.company_name}' created successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Configure White Label"
        context["is_create"] = True
        return context


class WhiteLabelUpdateView(AdminRequiredMixin, UpdateView):
    """Admin-only update of white-label configuration."""
    model = WhiteLabel
    form_class = WhiteLabelForm
    template_name = "accounts/whitelabel_form.html"
    success_url = reverse_lazy("accounts:whitelabel_list")

    def form_valid(self, form):
        messages.success(self.request, f"White-label branding for '{form.instance.company_name}' updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = f"Edit White Label: {self.object.company_name}"
        context["is_create"] = False
        return context


class WhiteLabelDeleteView(AdminRequiredMixin, DeleteView):
    """Admin-only deletion of white-label configuration."""
    model = WhiteLabel
    success_url = reverse_lazy("accounts:whitelabel_list")

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        name = obj.company_name
        res = super().delete(request, *args, **kwargs)
        messages.success(request, f"White-label configuration for '{name}' was deleted.")
        return res


# ---------------------------------------------------------------------------
# Priority Whitelist Management (Admin Only)
# ---------------------------------------------------------------------------

class WhitelistListView(AdminRequiredMixin, ListView):
    """Admin-only view to manage user-wise priority whitelisted phone numbers that never get cut."""
    model = WhitelistNumber
    template_name = "accounts/whitelist_list.html"
    context_object_name = "numbers_list"
    paginate_by = 25

    def get_queryset(self):
        qs = WhitelistNumber.objects.select_related("user", "created_by").order_by("-created_at")
        search = self.request.GET.get("q")
        if search:
            qs = qs.filter(
                Q(phone_number__icontains=search)
                | Q(description__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__company_name__icontains=search)
            )
        user_id = self.request.GET.get("user_id")
        if user_id:
            qs = qs.filter(user_id=user_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_query"] = self.request.GET.get("q", "")
        context["selected_user_id"] = self.request.GET.get("user_id", "")
        context["page_title"] = "Priority Whitelist"
        if "form" not in context:
            context["form"] = WhitelistNumberForm()
        context["total_whitelisted"] = WhitelistNumber.objects.count()
        context["users_list"] = User.objects.all().order_by("username")
        return context

    def post(self, request, *args, **kwargs):
        form = WhitelistNumberForm(request.POST)
        if form.is_valid():
            target_user = form.cleaned_data["user"]
            raw_text = form.cleaned_data["numbers"]
            description = form.cleaned_data.get("description", "").strip()

            import re
            tokens = re.split(r"[\r\n,;]+", raw_text)
            added_count = 0
            for token in tokens:
                digits = "".join(filter(str.isdigit, token.strip()))
                if digits:
                    if len(digits) == 10:
                        norm = "+91" + digits
                    elif len(digits) == 12 and digits.startswith("91"):
                        norm = "+" + digits
                    else:
                        norm = "+" + digits if not token.strip().startswith("+") else token.strip()
                    _, created = WhitelistNumber.objects.get_or_create(
                        user=target_user,
                        phone_number=norm,
                        defaults={"description": description, "created_by": request.user},
                    )
                    if created:
                        added_count += 1

            if added_count > 0:
                messages.success(request, f"Successfully added {added_count} number(s) to whitelist for {target_user.username}.")
            else:
                messages.info(request, f"No new numbers were added (numbers may already exist on {target_user.username}'s whitelist).")
            return redirect("accounts:whitelist_list")

        self.object_list = self.get_queryset()
        context = self.get_context_data(form=form)
        return self.render_to_response(context)


class WhitelistDeleteView(AdminRequiredMixin, DeleteView):
    """Admin-only deletion of a whitelisted phone number."""
    model = WhitelistNumber
    success_url = reverse_lazy("accounts:whitelist_list")

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        num = obj.phone_number
        uname = obj.user.username if obj.user else "Account"
        res = super().delete(request, *args, **kwargs)
        messages.success(request, f"Number '{num}' removed from {uname}'s whitelist.")
        return res


# ---------------------------------------------------------------------------
# KYC Management Views
# ---------------------------------------------------------------------------

class UserKYCView(LoginRequiredMixin, View):
    """View for every user to submit, view, and update their KYC details."""
    template_name = "accounts/kyc_details.html"

    def get(self, request, *args, **kwargs):
        kyc = getattr(request.user, "kyc", None)
        if kyc:
            form = UserKYCForm(instance=kyc)
        else:
            initial_data = {
                "company_name": request.user.company_name,
                "person_name": request.user.get_full_name(),
                "email": request.user.email,
                "phone": request.user.phone,
                "gst_number": request.user.GSTIN,
            }
            form = UserKYCForm(initial=initial_data)

        all_kycs = None
        if request.user.is_admin:
            all_kycs = UserKYC.objects.select_related("user").order_by("-updated_at")

        context = {
            "page_title": "KYC Verification",
            "form": form,
            "kyc": kyc,
            "all_kycs": all_kycs,
            "active_tab": request.GET.get("tab", "my_kyc"),
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        kyc = getattr(request.user, "kyc", None)
        if kyc:
            form = UserKYCForm(request.POST, instance=kyc)
        else:
            form = UserKYCForm(request.POST)

        if form.is_valid():
            kyc_obj = form.save(commit=False)
            kyc_obj.user = request.user
            if not kyc or kyc.status != UserKYC.Status.VERIFIED:
                kyc_obj.status = UserKYC.Status.PENDING
            kyc_obj.save()

            # Keep user profile fields aligned if helpful
            user_updated = False
            if not request.user.company_name and kyc_obj.company_name:
                request.user.company_name = kyc_obj.company_name
                user_updated = True
            if not request.user.phone and kyc_obj.phone:
                request.user.phone = kyc_obj.phone
                user_updated = True
            if not request.user.GSTIN and kyc_obj.gst_number:
                request.user.GSTIN = kyc_obj.gst_number
                user_updated = True
            if user_updated:
                request.user.save(update_fields=["company_name", "phone", "GSTIN"])

            messages.success(request, "KYC details saved successfully.")
            return redirect("accounts:kyc_details")

        all_kycs = None
        if request.user.is_admin:
            all_kycs = UserKYC.objects.select_related("user").order_by("-updated_at")

        context = {
            "page_title": "KYC Verification",
            "form": form,
            "kyc": kyc,
            "all_kycs": all_kycs,
            "active_tab": "my_kyc",
        }
        return render(request, self.template_name, context)


class UpdateKYCStatusView(AdminRequiredMixin, View):
    """Admin-only action to change status of a user's KYC (approve / reject)."""

    def post(self, request, pk, *args, **kwargs):
        kyc = get_object_or_404(UserKYC, pk=pk)
        new_status = request.POST.get("status")
        remarks = request.POST.get("remarks", "").strip()
        next_url = request.POST.get("next") or request.GET.get("next")

        if new_status in [UserKYC.Status.VERIFIED, UserKYC.Status.REJECTED, UserKYC.Status.PENDING]:
            kyc.status = new_status
            if remarks:
                kyc.remarks = remarks
            kyc.save(update_fields=["status", "remarks", "updated_at"])
            messages.success(request, f"KYC for {kyc.user.username} updated to '{kyc.get_status_display()}'.")
        else:
            messages.error(request, "Invalid KYC status specified.")

        if next_url:
            return redirect(next_url)
        return redirect("accounts:admin_kyc_list")


class AdminKYCListView(AdminRequiredMixin, ListView):
    """Dedicated Admin view to inspect and manage all submitted user KYCs across the platform."""
    model = UserKYC
    template_name = "accounts/admin_kyc_list.html"
    context_object_name = "kyc_list"
    paginate_by = 25

    def get_queryset(self):
        qs = UserKYC.objects.select_related("user").order_by("-updated_at")
        search = self.request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(
                Q(company_name__icontains=search)
                | Q(person_name__icontains=search)
                | Q(user__username__icontains=search)
                | Q(pan_card__icontains=search)
                | Q(aadhar__icontains=search)
                | Q(phone__icontains=search)
                | Q(email__icontains=search)
                | Q(gst_number__icontains=search)
            )
        status_filter = self.request.GET.get("status", "").strip()
        if status_filter in [UserKYC.Status.PENDING, UserKYC.Status.VERIFIED, UserKYC.Status.REJECTED]:
            qs = qs.filter(status=status_filter)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_qs = UserKYC.objects.all()
        context["search_query"] = self.request.GET.get("q", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["total_count"] = all_qs.count()
        context["pending_count"] = all_qs.filter(status=UserKYC.Status.PENDING).count()
        context["verified_count"] = all_qs.filter(status=UserKYC.Status.VERIFIED).count()
        context["rejected_count"] = all_qs.filter(status=UserKYC.Status.REJECTED).count()
        context["page_title"] = "KYC Requests Directory"
        return context





