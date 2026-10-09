"""Forms for Client Bot Onboarding Applications."""
from django import forms
from .models import OnboardingApplication

TAILWIND_INPUT = (
    "w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-900 "
    "placeholder-slate-400 text-xs font-medium focus:outline-none focus:border-blue-600 "
    "focus:ring-4 focus:ring-blue-100 transition-all shadow-2xs"
)
TAILWIND_FILE = (
    "w-full text-xs text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl "
    "file:border-0 file:text-xs file:font-semibold file:bg-blue-50 file:text-blue-700 "
    "hover:file:bg-blue-100 file:cursor-pointer cursor-pointer border border-slate-200 rounded-xl p-1 bg-white"
)


class OnboardingApplicationForm(forms.ModelForm):
    """Client form replicating the operator's Google Form for bot onboarding."""

    MESSAGE_TYPE_CHOICES = [
        ("Promotional", "Promotional"),
        ("Transactional", "Transactional"),
        ("OTP", "OTP"),
    ]

    bot_message_types = forms.MultipleChoiceField(
        choices=MESSAGE_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "rounded text-blue-600 focus:ring-blue-500"}),
        label="Bot Message Type",
        help_text="Select one or more message categories your bot will transmit.",
    )

    class Meta:
        model = OnboardingApplication
        fields = [
            # Bot & Brand Identity
            "brand_name",
            "bot_name",
            # Brand SPOC Details
            "brand_spoc_name",
            "brand_spoc_designation",
            "brand_spoc_email",
            # KYC Documents
            "gst_certificate",
            "pan_card",
            # Visual Assets
            "bot_logo",
            "banner_image",
            # Styling & Description
            "short_description",
            "color_code",
            # Contact Information
            "primary_phone",
            "primary_phone_label",
            "primary_email",
            "primary_email_label",
            "primary_website",
            "primary_website_label",
            # Legal & Language
            "terms_conditions_url",
            "privacy_url",
            "languages_supported",
            # Verification Screenshot
            "opt_in_screenshot",
        ]
        widgets = {
            "brand_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Official Brand / Enterprise Name"}),
            "bot_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Bot Name (max 40 chars, at least 1 letter)", "maxlength": "40"}),
            "brand_spoc_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Your answer"}),
            "brand_spoc_designation": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Your answer"}),
            "brand_spoc_email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Your answer"}),
            "gst_certificate": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": ".pdf,application/pdf"}),
            "pan_card": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": ".pdf,application/pdf,image/png,image/jpeg,image/jpg"}),
            "bot_logo": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": "image/png,image/jpeg,image/jpg"}),
            "banner_image": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": "image/png,image/jpeg,image/jpg"}),
            "short_description": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Brief description of bot utility (max 100 chars)", "maxlength": "100"}),
            "color_code": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "#2563EB", "type": "color"}),
            "primary_phone": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "+91 9876543210"}),
            "primary_phone_label": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Support Phone", "maxlength": "25"}),
            "primary_email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "support@brand.com"}),
            "primary_email_label": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Customer Support", "maxlength": "25"}),
            "primary_website": forms.URLInput(attrs={"class": TAILWIND_INPUT, "placeholder": "https://brand.com"}),
            "primary_website_label": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Official Website", "maxlength": "25"}),
            "terms_conditions_url": forms.URLInput(attrs={"class": TAILWIND_INPUT, "placeholder": "https://brand.com/terms"}),
            "privacy_url": forms.URLInput(attrs={"class": TAILWIND_INPUT, "placeholder": "https://brand.com/privacy"}),
            "languages_supported": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. English, Hindi"}),
            "opt_in_screenshot": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": "image/*,.pdf"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and self.instance.bot_message_type:
            self.fields["bot_message_types"].initial = self.instance.get_message_types_list()

        # Set requirements for KYC docs & Brand SPOC
        self.fields["brand_spoc_name"].required = True
        self.fields["brand_spoc_designation"].required = False
        self.fields["brand_spoc_email"].required = False

        if not (self.instance and self.instance.pk and self.instance.gst_certificate):
            self.fields["gst_certificate"].required = True
        else:
            self.fields["gst_certificate"].required = False

        if not (self.instance and self.instance.pk and self.instance.pan_card):
            self.fields["pan_card"].required = True
        else:
            self.fields["pan_card"].required = False

        # Sensible pre-filling if new application
        if not (self.instance and self.instance.pk) and user:
            self.fields["brand_name"].initial = getattr(user, "company_name", "")
            self.fields["brand_spoc_name"].initial = user.get_full_name() or user.username
            self.fields["brand_spoc_email"].initial = user.email or ""
            self.fields["primary_phone"].initial = getattr(user, "phone", "")
            self.fields["primary_email"].initial = user.email or ""

    def clean_gst_certificate(self):
        f = self.cleaned_data.get("gst_certificate")
        if f:
            if hasattr(f, "size") and f.size > 10 * 1024 * 1024:
                raise forms.ValidationError("GST Certificate must not exceed 10 MB.")
            if hasattr(f, "name"):
                name = f.name.lower()
                if not name.endswith(".pdf"):
                    raise forms.ValidationError("GST Certificate must be a PDF file.")
        return f

    def clean_pan_card(self):
        f = self.cleaned_data.get("pan_card")
        if f:
            if hasattr(f, "size") and f.size > 10 * 1024 * 1024:
                raise forms.ValidationError("PAN Card file must not exceed 10 MB.")
            if hasattr(f, "name"):
                name = f.name.lower()
                valid_exts = (".pdf", ".png", ".jpg", ".jpeg")
                if not any(name.endswith(ext) for ext in valid_exts):
                    raise forms.ValidationError("PAN Card must be a PDF, JPG, or PNG file.")
        return f

    def clean_bot_name(self):
        bot_name = self.cleaned_data.get("bot_name", "").strip()
        if not any(c.isalpha() for c in bot_name):
            raise forms.ValidationError("Bot name must contain at least 1 alphabet character.")
        return bot_name

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.user:
            instance.user = self.user
        types = self.cleaned_data.get("bot_message_types", [])
        instance.bot_message_type = ", ".join(types)
        if commit:
            instance.save()
        return instance


class OnboardingStatusUpdateForm(forms.ModelForm):
    """Admin form to update operator approval status and forward notes."""

    class Meta:
        model = OnboardingApplication
        fields = ["status", "operator_name", "operator_reference_id", "admin_notes"]
        widgets = {
            "status": forms.Select(attrs={"class": TAILWIND_INPUT}),
            "operator_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Vodafone / VI, OneXtel, Jio, etc."}),
            "operator_reference_id": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Operator Bot ID or Ticket #"}),
            "admin_notes": forms.Textarea(attrs={"class": TAILWIND_INPUT, "rows": "3", "placeholder": "Internal notes, verification feedback, or operator responses..."}),
        }
