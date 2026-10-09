"""Views for wallet statements, balance adjustments, credit transfers, and rate plans."""
import csv
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import FormView, ListView, TemplateView

from accounts.mixins import AdminRequiredMixin, ResellerRequiredMixin, RoleRequiredMixin
from .forms import (
    AssignCreditForm,
    RatePlansForm,
    SenderProfileForm,
    TransferCreditForm,
    WalletAdjustmentForm,
    WalletStatementFilterForm,
)
from .models import RatePlan, SenderProfile, Wallet, WalletTransaction
from .services import (
    InsufficientBalanceError,
    InvalidAmountError,
    RateValidationError,
    credit,
    debit,
    set_user_rate,
    transfer,
)

User = get_user_model()


class WalletStatementView(RoleRequiredMixin, ListView):
    """Financial ledger statement view with filtering and CSV export.

    Role scoping:
    - Admin: Can inspect all transactions across all users.
    - Reseller: Can inspect own transactions and transactions belonging to their clients.
    - User: Can only inspect transactions for their own wallet.
    """

    model = WalletTransaction
    template_name = "wallet/statement.html"
    context_object_name = "transactions"
    paginate_by = 25
    allowed_roles = ["admin", "reseller", "user"]

    def get_queryset(self):
        user = self.request.user
        qs = WalletTransaction.objects.select_related("wallet__user", "created_by").order_by("-created_at")

        # Role scoping
        if not user.is_admin:
            if user.role == User.Role.RESELLER:
                qs = qs.filter(
                    Q(wallet__user=user) | Q(wallet__user__parent_reseller=user)
                )
            else:
                qs = qs.filter(wallet__user=user)

        # Filters
        self.filter_form = WalletStatementFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            start_date = self.filter_form.cleaned_data.get("start_date")
            end_date = self.filter_form.cleaned_data.get("end_date")
            tx_type = self.filter_form.cleaned_data.get("tx_type")

            if start_date:
                qs = qs.filter(created_at__date__gte=start_date)
            if end_date:
                qs = qs.filter(created_at__date__lte=end_date)
            if tx_type:
                qs = qs.filter(type=tx_type)

        target_user_id = self.request.GET.get("user_id")
        if target_user_id and (user.is_admin or user.is_reseller):
            qs = qs.filter(wallet__user_id=target_user_id)

        return qs

    def get(self, request, *args, **kwargs):
        # CSV Export Handling
        if request.GET.get("export") == "csv":
            return self.export_csv(self.get_queryset())
        return super().get(request, *args, **kwargs)

    def export_csv(self, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="wallet_statement.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "Timestamp",
            "Account Email",
            "Company",
            "Type",
            "Amount (INR)",
            "Balance After (INR)",
            "Reference Type",
            "Reference ID",
            "Remarks",
            "Initiated By",
        ])

        for tx in queryset:
            writer.writerow([
                tx.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                tx.wallet.user.email,
                tx.wallet.user.company_name or "-",
                tx.get_type_display(),
                f"{tx.amount:.4f}",
                f"{tx.balance_after:.4f}",
                tx.reference_type or "-",
                tx.reference_id or "-",
                tx.remarks or "-",
                tx.created_by.email if tx.created_by else "System",
            ])

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = getattr(self, "filter_form", WalletStatementFilterForm(self.request.GET))
        context["page_title"] = "Wallet Ledger Statement"
        return context


class AssignCreditView(RoleRequiredMixin, FormView):
    """View to assign/add credit amounts to the wallet of any user or reseller."""

    allowed_roles = ["superadmin", "admin", "reseller"]
    template_name = "wallet/assign_credit.html"
    form_class = AssignCreditForm
    success_url = reverse_lazy("wallet:assign_credit")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["actor"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        target_id = self.request.GET.get("user_id") or self.request.GET.get("client_id")
        if target_id:
            initial["target_user"] = target_id
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["page_title"] = "Assign Wallet Credit"
        context["is_superadmin"] = user.is_admin

        # Fetch recent transactions initiated by or involving this user
        if user.is_admin:
            context["recent_transactions"] = WalletTransaction.objects.select_related(
                "wallet__user", "created_by"
            ).filter(
                type__in=[
                    WalletTransaction.TransactionType.CREDIT,
                    WalletTransaction.TransactionType.TRANSFER_IN,
                    WalletTransaction.TransactionType.DEBIT,
                ]
            ).order_by("-created_at")[:10]
        else:
            context["recent_transactions"] = WalletTransaction.objects.select_related(
                "wallet__user", "created_by"
            ).filter(
                Q(created_by=user) | Q(wallet__user=user)
            ).order_by("-created_at")[:10]

        return context

    def form_valid(self, form):
        actor = self.request.user
        target_user = form.cleaned_data["target_user"]
        action = form.cleaned_data["action"]
        amount = form.cleaned_data["amount"]
        remarks = form.cleaned_data.get("remarks") or ""

        try:
            if actor.is_admin:
                if action == "credit":
                    credit(
                        user=target_user,
                        amount=amount,
                        remarks=remarks or f"Assigned by @{actor.username}",
                        created_by=actor,
                        reference_type="admin_assign_credit",
                    )
                    messages.success(
                        self.request,
                        f"Successfully added ₹{amount:.2f} credit to {target_user.company_name or target_user.username} (@{target_user.username})."
                    )
                else:
                    debit(
                        user=target_user,
                        amount=amount,
                        remarks=remarks or f"Deducted by @{actor.username}",
                        created_by=actor,
                        reference_type="admin_deduct_credit",
                    )
                    messages.success(
                        self.request,
                        f"Successfully deducted ₹{amount:.2f} from {target_user.company_name or target_user.username} (@{target_user.username})."
                    )
            elif actor.is_reseller:
                # Reseller transfers from their balance to their client / sub-reseller
                transfer(
                    from_user=actor,
                    to_user=target_user,
                    amount=amount,
                    remarks=remarks or f"Credit allocated by reseller @{actor.username}",
                    created_by=actor,
                )
                messages.success(
                    self.request,
                    f"Successfully allocated ₹{amount:.2f} credit to {target_user.company_name or target_user.username} (@{target_user.username})."
                )
            return redirect("wallet:assign_credit")
        except InsufficientBalanceError as e:
            messages.error(self.request, f"Operation failed: {e}")
            return self.form_invalid(form)
        except Exception as e:
            messages.error(self.request, f"Error assigning credit: {e}")
            return self.form_invalid(form)


class ResellerCreditTransferView(ResellerRequiredMixin, FormView):
    """Reseller view to allocate prepaid credits to a direct client."""

    template_name = "wallet/transfer.html"
    form_class = TransferCreditForm
    success_url = reverse_lazy("wallet:statement")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["reseller"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        client_id = self.request.GET.get("client_id")
        if client_id:
            initial["client"] = client_id
        return initial

    def form_valid(self, form):
        client = form.cleaned_data["client"]
        amount = form.cleaned_data["amount"]
        remarks = form.cleaned_data.get("remarks", "")

        try:
            transfer(
                from_user=self.request.user,
                to_user=client,
                amount=amount,
                remarks=remarks,
                created_by=self.request.user,
            )
            messages.success(
                self.request,
                f"Successfully transferred {amount} INR to {client.company_name or client.email}."
            )
            return redirect("accounts:client_list")
        except InsufficientBalanceError as e:
            messages.error(self.request, f"Transfer failed: {e}")
            return self.form_invalid(form)
        except Exception as e:
            messages.error(self.request, f"Transfer error: {e}")
            return self.form_invalid(form)


class AdminAdjustWalletView(AdminRequiredMixin, FormView):
    """Admin view to credit or deduct wallet funds with mandatory remarks."""

    template_name = "wallet/adjust_wallet.html"
    form_class = WalletAdjustmentForm

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.target_user = get_object_or_404(User, id=kwargs["user_id"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_user"] = self.target_user
        context["target_wallet"] = getattr(self.target_user, "wallet", None)
        return context

    def form_valid(self, form):
        action = form.cleaned_data["action"]
        amount = form.cleaned_data["amount"]
        remarks = form.cleaned_data["remarks"]

        try:
            if action == "credit":
                credit(
                    user=self.target_user,
                    amount=amount,
                    remarks=remarks,
                    created_by=self.request.user,
                    reference_type="admin_adjustment",
                )
                messages.success(self.request, f"Successfully credited {amount} INR to {self.target_user.email}.")
            else:
                debit(
                    user=self.target_user,
                    amount=amount,
                    remarks=remarks,
                    created_by=self.request.user,
                    reference_type="admin_adjustment",
                )
                messages.success(self.request, f"Successfully deducted {amount} INR from {self.target_user.email}.")
            return redirect("accounts:user_list")
        except InsufficientBalanceError as e:
            messages.error(self.request, f"Adjustment failed: {e}")
            return self.form_invalid(form)
        except Exception as e:
            messages.error(self.request, f"An unexpected error occurred: {e}")
            return self.form_invalid(form)


class ManageRatePlansView(RoleRequiredMixin, View):
    """View to set and view Rate Plans (Promotional, Transactional, OTP).

    - Admins can set rates and cost rates for any user/reseller.
    - Resellers can set rates for their direct clients (client rate >= reseller rate).
    """

    allowed_roles = ["admin", "reseller"]
    template_name = "wallet/manage_rates.html"

    def get_target_user(self, request, user_id):
        target = get_object_or_404(User, id=user_id)
        if request.user.is_admin:
            return target
        if request.user.is_reseller and target.parent_reseller == request.user:
            return target
        raise PermissionDenied("You do not have permission to manage rates for this user.")

    def get(self, request, user_id):
        if request.user.is_admin:
            return redirect(f"{reverse('accounts:user_list')}?open_rates={user_id}")
        target_user = self.get_target_user(request, user_id)
        rates = {rp.message_type: rp for rp in target_user.rate_plans.all()}

        # If reseller is viewing, fetch reseller's own baseline rates for comparison
        reseller_rates = {}
        if target_user.parent_reseller:
            reseller_rates = {rp.message_type: rp for rp in target_user.parent_reseller.rate_plans.all()}

        initial = {
            "promotional_rate": rates.get("PROMOTIONAL").rate if rates.get("PROMOTIONAL") else Decimal("0.2500"),
            "promotional_cost_rate": rates.get("PROMOTIONAL").cost_rate if rates.get("PROMOTIONAL") else Decimal("0.1800"),
            "transactional_rate": rates.get("TRANSACTIONAL").rate if rates.get("TRANSACTIONAL") else Decimal("0.2000"),
            "transactional_cost_rate": rates.get("TRANSACTIONAL").cost_rate if rates.get("TRANSACTIONAL") else Decimal("0.1500"),
            "otp_rate": rates.get("OTP").rate if rates.get("OTP") else Decimal("0.1500"),
            "otp_cost_rate": rates.get("OTP").cost_rate if rates.get("OTP") else Decimal("0.1000"),
        }
        form = RatePlansForm(initial=initial)

        return render(
            request,
            self.template_name,
            {
                "target_user": target_user,
                "form": form,
                "reseller_rates": reseller_rates,
                "is_admin": request.user.is_admin,
            },
        )

    def post(self, request, user_id):
        target_user = self.get_target_user(request, user_id)
        form = RatePlansForm(request.POST)

        if form.is_valid():
            try:
                # 1. Promotional
                set_user_rate(
                    user=target_user,
                    message_type="PROMOTIONAL",
                    rate=form.cleaned_data["promotional_rate"],
                    cost_rate=form.cleaned_data.get("promotional_cost_rate") or Decimal("0.0000"),
                )
                # 2. Transactional
                set_user_rate(
                    user=target_user,
                    message_type="TRANSACTIONAL",
                    rate=form.cleaned_data["transactional_rate"],
                    cost_rate=form.cleaned_data.get("transactional_cost_rate") or Decimal("0.0000"),
                )
                # 3. OTP
                set_user_rate(
                    user=target_user,
                    message_type="OTP",
                    rate=form.cleaned_data["otp_rate"],
                    cost_rate=form.cleaned_data.get("otp_cost_rate") or Decimal("0.0000"),
                )

                messages.success(request, f"Rate plans successfully saved for {target_user.email}.")
                next_url = request.POST.get("next") or request.GET.get("next")
                if next_url:
                    return redirect(next_url)
                if request.user.is_admin:
                    return redirect("accounts:user_list")
                return redirect("accounts:client_list")
            except RateValidationError as e:
                messages.error(request, str(e))
            except Exception as e:
                messages.error(request, f"Error saving rate plan: {e}")

        return render(
            request,
            self.template_name,
            {
                "target_user": target_user,
                "form": form,
                "is_admin": request.user.is_admin,
            },
        )


class ManageSenderProfilesView(AdminRequiredMixin, View):
    """Admin view to view and assign Sender / Bot Profiles to users."""

    template_name = "wallet/sender_profiles.html"

    def get(self, request, user_id):
        if request.user.is_admin:
            return redirect(f"{reverse('accounts:user_list')}?open_senders={user_id}")
        target_user = get_object_or_404(User, id=user_id)
        profiles = target_user.sender_profiles.all()
        form = SenderProfileForm()
        return render(
            request,
            self.template_name,
            {
                "target_user": target_user,
                "profiles": profiles,
                "form": form,
            },
        )

    def post(self, request, user_id):
        target_user = get_object_or_404(User, id=user_id)
        form = SenderProfileForm(request.POST)
        next_url = request.POST.get("next") or request.GET.get("next")
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = target_user
            profile.save()
            messages.success(request, f"Sender profile '{profile.sender_profile_name}' assigned to {target_user.email}.")
            if next_url:
                return redirect(next_url)
            return redirect("wallet:sender_profiles", user_id=target_user.id)

        if next_url:
            messages.error(request, "Failed to add sender profile. Please check the inputs.")
            return redirect(next_url)

        profiles = target_user.sender_profiles.all()
        return render(
            request,
            self.template_name,
            {
                "target_user": target_user,
                "profiles": profiles,
                "form": form,
            },
        )


class ToggleSenderProfileActiveView(AdminRequiredMixin, View):
    """Toggle active status for a sender profile."""

    def post(self, request, profile_id):
        profile = get_object_or_404(SenderProfile, id=profile_id)
        profile.is_active = not profile.is_active
        profile.save(update_fields=["is_active"])
        messages.success(
            request,
            f"Sender profile '{profile.sender_profile_name}' is now {'Active' if profile.is_active else 'Suspended'}."
        )
        next_url = request.POST.get("next") or request.GET.get("next")
        if next_url:
            return redirect(next_url)
        return redirect("wallet:sender_profiles", user_id=profile.user_id)
