"""Forms for wallet operations, transfers, rate plans, and statements."""
from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from .models import RatePlan, SenderProfile, WalletTransaction

User = get_user_model()

TAILWIND_INPUT = (
    "w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-900 "
    "placeholder-slate-400 text-xs font-medium focus:outline-none focus:border-blue-600 "
    "focus:ring-4 focus:ring-blue-100 transition-all shadow-2xs"
)
TAILWIND_SELECT = (
    "w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-900 "
    "text-xs font-medium focus:outline-none focus:border-blue-600 focus:ring-4 "
    "focus:ring-blue-100 transition-all shadow-2xs"
)
TAILWIND_TEXTAREA = (
    "w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-900 "
    "placeholder-slate-400 text-xs font-medium focus:outline-none focus:border-blue-600 "
    "focus:ring-4 focus:ring-blue-100 transition-all shadow-2xs h-24 resize-none"
)
TAILWIND_CHECKBOX = "rounded-md border-slate-300 text-blue-600 focus:ring-blue-500 h-4 w-4"



class AssignCreditForm(forms.Form):
    """Form to add credit amounts to the wallet of any user or reseller."""

    ACTION_CHOICES = (
        ("credit", "Add Credit (+)"),
        ("debit", "Deduct Credit (-)"),
    )

    target_user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Target Account (User or Reseller)",
        empty_label="-- Select User or Reseller --",
    )
    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        initial="credit",
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Operation",
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Enter amount in ₹ (e.g. 500.00)", "step": "0.01"}),
        label="Amount (₹)",
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": TAILWIND_TEXTAREA, "placeholder": "Enter reason or reference notes (e.g. Bank transfer, manual allocation, monthly top-up)..."}
        ),
        label="Remarks / Reference",
    )

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        if actor:
            if actor.is_admin:
                # Superadmin can add amounts to any User or Reseller
                self.fields["target_user"].queryset = User.objects.filter(
                    role__in=[User.Role.USER, User.Role.RESELLER],
                    is_active=True,
                ).select_related("wallet").order_by("role", "company_name", "username")
            elif actor.is_reseller:
                # Reseller can add amounts to their own direct clients/sub-resellers
                self.fields["target_user"].queryset = User.objects.filter(
                    parent_reseller=actor,
                    is_active=True,
                ).select_related("wallet").order_by("role", "company_name", "username")
                self.fields["action"].choices = (("credit", "Add Credit (+)"),)
            else:
                self.fields["target_user"].queryset = User.objects.none()

            self.fields["target_user"].label_from_instance = lambda obj: (
                f"{obj.company_name or obj.get_full_name() or obj.username} "
                f"(@{obj.username} | {obj.get_role_display()} | Current: ₹{obj.wallet.balance if hasattr(obj, 'wallet') and obj.wallet else '0.00'})"
            )


class WalletAdjustmentForm(forms.Form):
    """Admin form to credit or deduct wallet funds with mandatory remarks."""

    ACTION_CHOICES = (
        ("credit", "Add Credit (+)"),
        ("debit", "Deduct Funds (-)"),
    )

    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Action",
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.00", "step": "0.01"}),
        label="Amount (INR)",
    )
    remarks = forms.CharField(
        required=True,
        widget=forms.Textarea(
            attrs={"class": TAILWIND_TEXTAREA, "placeholder": "Reason for adjustment (mandatory audit trail)..."}
        ),
        label="Mandatory Remark",
    )


class TransferCreditForm(forms.Form):
    """Reseller form to transfer balance directly to an onboarded client."""

    client = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Target Client",
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.00", "step": "0.01"}),
        label="Transfer Amount (INR)",
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": TAILWIND_TEXTAREA, "placeholder": "Optional transfer notes..."}
        ),
        label="Transfer Remarks",
    )

    def __init__(self, *args, reseller=None, **kwargs):
        super().__init__(*args, **kwargs)
        if reseller:
            self.fields["client"].queryset = User.objects.filter(parent_reseller=reseller, is_active=True)
            self.fields["client"].label_from_instance = lambda obj: f"{obj.company_name or obj.get_full_name()} ({obj.email})"


class RatePlansForm(forms.Form):
    """Configure Promotional, Transactional, and OTP rates for a user."""

    # Promotional
    promotional_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.2500", "step": "0.0001"}),
        label="Promotional Rate (INR)",
    )
    promotional_cost_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0000"),
        required=False,
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.1800", "step": "0.0001"}),
        label="Promotional Cost Rate (INR)",
    )

    # Transactional
    transactional_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.2000", "step": "0.0001"}),
        label="Transactional Rate (INR)",
    )
    transactional_cost_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0000"),
        required=False,
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.1500", "step": "0.0001"}),
        label="Transactional Cost Rate (INR)",
    )

    # OTP
    otp_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.1500", "step": "0.0001"}),
        label="OTP Rate (INR)",
    )
    otp_cost_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0000"),
        required=False,
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.1000", "step": "0.0001"}),
        label="OTP Cost Rate (INR)",
    )


class SenderProfileForm(forms.ModelForm):
    """Form for adding and editing Sender Profiles."""

    class Meta:
        model = SenderProfile
        fields = ["sender_id", "sender_profile_name", "is_active"]
        widgets = {
            "sender_id": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. acme_rcs_bot_01"}),
            "sender_profile_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. Acme Verified Brand"}),
            "is_active": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
        }


class WalletStatementFilterForm(forms.Form):
    """Filter form for wallet transaction statement."""

    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": TAILWIND_INPUT, "type": "date"}),
        label="From Date",
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": TAILWIND_INPUT, "type": "date"}),
        label="To Date",
    )
    tx_type = forms.ChoiceField(
        required=False,
        choices=[("", "All Types")] + list(WalletTransaction.TransactionType.choices),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Transaction Type",
    )
