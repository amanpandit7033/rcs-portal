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

    target_user = forms.CharField(
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "Search by username...",
                "list": "target-user-candidates",
                "autocomplete": "off",
            }
        ),
        label="Target Username",
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
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Enter amount in ₹ (e.g. 500.00)", "step": "any"}),
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
        if actor and actor.is_reseller:
            self.fields["action"].choices = (("credit", "Add Credit (+)"),)

    def clean_target_user(self):
        val = str(self.cleaned_data.get("target_user", "")).strip()
        if not val:
            raise forms.ValidationError("Please enter or select a target username.")

        if val.startswith("@"):
            val = val[1:].strip()

        target = None
        if val.isdigit():
            target = User.objects.filter(id=val).select_related("wallet").first()
        if not target:
            target = User.objects.filter(username__iexact=val).select_related("wallet").first()

        if not target:
            raise forms.ValidationError(f"User '{val}' not found.")

        actor = getattr(self, "actor", None)
        if actor:
            if actor.is_admin:
                if not target.is_active:
                    raise forms.ValidationError(f"User '@{target.username}' is not active.")
            elif actor.is_reseller or actor.is_distributor:
                # Reseller or Distributor can assign only to direct clients or sub-accounts under them
                allowed = User.objects.filter(
                    parent_reseller=actor,
                    is_active=True,
                ).values_list("id", flat=True)
                if target.id not in allowed:
                    raise forms.ValidationError(f"User '@{target.username}' is not under your management.")
            else:
                raise forms.ValidationError("You do not have permission to assign credit.")

        return target


class RatePlansForm(forms.Form):
    """Configure Promotional, Transactional, and OTP rates for a user."""

    # Promotional
    promotional_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.2500", "step": "any"}),
        label="Promotional Rate (INR)",
    )

    # Transactional
    transactional_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.2000", "step": "any"}),
        label="Transactional Rate (INR)",
    )

    # OTP
    otp_rate = forms.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "0.1500", "step": "any"}),
        label="OTP Rate (INR)",
    )


class SenderProfileForm(forms.ModelForm):
    """Form for adding and editing Sender Profiles."""

    class Meta:
        model = SenderProfile
        fields = ["sender_id", "sender_profile_name", "api_key", "is_active"]
        widgets = {
            "sender_id": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. acme_rcs_bot_01"}),
            "sender_profile_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. Acme Verified Brand"}),
            "api_key": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. onex_live_api_key_..."}),
            "is_active": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
        }


class WalletStatementFilterForm(forms.Form):
    """Filter form for wallet credit/debit transaction statement."""

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
        choices=[
            ("", "All Operations"),
            ("credit", "Credit / Added (+)"),
            ("debit", "Debit / Deducted (-)"),
        ],
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Operation",
    )


class RefundLedgerFilterForm(forms.Form):
    """Filter form for refund and cutting ledger."""

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Search username or campaign..."}),
        label="Search",
    )
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
    refund_type = forms.ChoiceField(
        required=False,
        choices=[("", "All Refund Types"), ("failed", "Failed Message Refund"), ("cutting", "Cutting Refund / Rebate")],
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Refund Type",
    )
