"""Authentication and account forms."""
from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm as BasePasswordChangeForm,
    PasswordResetForm as BasePasswordResetForm,
    SetPasswordForm as BaseSetPasswordForm,
)


class LoginForm(AuthenticationForm):
    """Custom login form styled with Tailwind for username & password authentication."""

    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(
            attrs={
                "class": "w-full pl-11 pr-4 py-3 rounded-xl border border-slate-200 bg-slate-50/50 text-slate-900 placeholder-slate-400 text-sm font-medium focus:outline-none focus:bg-white focus:border-blue-600 focus:ring-4 focus:ring-blue-100 transition-all shadow-sm",
                "placeholder": "Enter your username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "w-full pl-11 pr-11 py-3 rounded-xl border border-slate-200 bg-slate-50/50 text-slate-900 placeholder-slate-400 text-sm font-medium focus:outline-none focus:bg-white focus:border-blue-600 focus:ring-4 focus:ring-blue-100 transition-all shadow-sm",
                "placeholder": "••••••••",
            }
        ),
    )


class PasswordResetForm(BasePasswordResetForm):
    """Password reset form styled with clean light Tailwind."""

    email = forms.EmailField(
        label="Email Address",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "class": "w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-900 placeholder-slate-400 text-xs font-medium focus:outline-none focus:border-blue-600 focus:ring-4 focus:ring-blue-100 transition-all shadow-2xs",
                "placeholder": "name@company.com",
                "autofocus": True,
            }
        ),
    )


class SetPasswordForm(BaseSetPasswordForm):
    """Set new password form styled with clean light Tailwind."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({
                "class": "w-full px-3.5 py-2.5 rounded-xl border border-slate-200 bg-white text-slate-900 placeholder-slate-400 text-xs font-medium focus:outline-none focus:border-blue-600 focus:ring-4 focus:ring-blue-100 transition-all shadow-2xs",
                "placeholder": "••••••••",
            })


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
TAILWIND_CHECKBOX = "rounded-md border-slate-300 text-blue-600 focus:ring-blue-500 h-4 w-4"



class UserCreateForm(forms.ModelForm):
    """Admin form to create Users and Resellers with initial password."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Initial password..."}),
        help_text="Temporary or permanent initial password",
    )

    class Meta:
        from .models import User
        model = User
        fields = [
            "username",
            "email",
            "password",
            "role",
            "parent_reseller",
            "company_name",
            "first_name",
            "last_name",
            "phone",
            "GSTIN",
            "state",
            "is_active",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "username"}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "user@company.com"}),
            "role": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "parent_reseller": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Acme Corp Ltd"}),
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Last Name"}),
            "phone": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "+91 9876543210"}),
            "GSTIN": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "27ABCDE1234F1Z5"}),
            "state": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Maharashtra"}),
            "is_active": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import User
        self.fields["role"].choices = [
            (User.Role.RESELLER, "Reseller"),
            (User.Role.USER, "User"),
        ]
        self.fields["parent_reseller"].queryset = User.objects.filter(role=User.Role.RESELLER)
        self.fields["parent_reseller"].required = False

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    """Admin form to modify user details and optionally reset password."""

    password = forms.CharField(
        label="New Password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "Leave blank to keep existing password...",
                "autocomplete": "new-password",
            }
        ),
        help_text="Leave blank to keep existing password intact.",
    )

    class Meta:
        from .models import User
        model = User
        fields = [
            "username",
            "email",
            "company_name",
            "first_name",
            "last_name",
            "phone",
            "GSTIN",
            "state",
            "role",
            "parent_reseller",
            "is_active",
            "can_use_low_price",
            "is_low_price_applied",
            "low_price_start_from",
            "low_price_percentage",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT}),
            "role": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "parent_reseller": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "phone": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "GSTIN": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "state": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "is_active": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "can_use_low_price": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "is_low_price_applied": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "low_price_start_from": forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. 1000", "min": "0"}),
            "low_price_percentage": forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. 10.00", "step": "0.01", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import User
        self.fields["role"].choices = [
            (User.Role.RESELLER, "Reseller"),
            (User.Role.USER, "User"),
        ]
        self.fields["parent_reseller"].queryset = User.objects.filter(role=User.Role.RESELLER)
        self.fields["parent_reseller"].required = False

    def save(self, commit=True):
        user = super().save(commit=False)
        new_pwd = self.cleaned_data.get("password")
        if new_pwd and new_pwd.strip():
            user.set_password(new_pwd)
        if commit:
            user.save()
        return user


class ClientCreateForm(forms.ModelForm):
    """Reseller form to register a new direct client or sub-reseller."""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Initial password..."}),
        help_text="Initial password for account login",
    )

    class Meta:
        from .models import User
        model = User
        fields = [
            "username",
            "email",
            "password",
            "role",
            "company_name",
            "first_name",
            "last_name",
            "phone",
            "GSTIN",
            "state",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "username"}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "account@enterprise.com"}),
            "role": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Enterprise Ltd"}),
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Last Name"}),
            "phone": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "+91 9876543210"}),
            "GSTIN": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "GSTIN"}),
            "state": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "State"}),
        }

    def __init__(self, *args, reseller=None, **kwargs):
        self.reseller = reseller
        super().__init__(*args, **kwargs)
        from .models import User
        self.fields["role"].choices = [
            (User.Role.USER, "User"),
            (User.Role.RESELLER, "Reseller"),
        ]
        self.fields["role"].initial = User.Role.USER

    def save(self, commit=True):
        from .models import User
        client = super().save(commit=False)
        client.role = self.cleaned_data.get("role", User.Role.USER)
        client.parent_reseller = self.reseller
        client.set_password(self.cleaned_data["password"])
        if commit:
            client.save()
        return client


class ClientEditForm(forms.ModelForm):
    """Reseller form to edit their client or sub-reseller details."""

    password = forms.CharField(
        label="New Password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": TAILWIND_INPUT,
                "placeholder": "Leave blank to keep existing password...",
                "autocomplete": "new-password",
            }
        ),
        help_text="Leave blank to keep existing password intact.",
    )

    class Meta:
        from .models import User
        model = User
        fields = [
            "username",
            "email",
            "role",
            "company_name",
            "first_name",
            "last_name",
            "phone",
            "GSTIN",
            "state",
            "is_active",
            "can_use_low_price",
            "is_low_price_applied",
            "low_price_start_from",
            "low_price_percentage",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT}),
            "role": forms.Select(attrs={"class": TAILWIND_SELECT}),
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "phone": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "GSTIN": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "state": forms.TextInput(attrs={"class": TAILWIND_INPUT}),
            "is_active": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "can_use_low_price": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "is_low_price_applied": forms.CheckboxInput(attrs={"class": TAILWIND_CHECKBOX}),
            "low_price_start_from": forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. 1000", "min": "0"}),
            "low_price_percentage": forms.NumberInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. 10.00", "step": "0.01", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import User
        self.fields["role"].choices = [
            (User.Role.USER, "User"),
            (User.Role.RESELLER, "Reseller"),
        ]

    def save(self, commit=True):
        client = super().save(commit=False)
        new_pwd = self.cleaned_data.get("password")
        if new_pwd and new_pwd.strip():
            client.set_password(new_pwd)
        if commit:
            client.save()
        return client


class WhitelistNumberForm(forms.Form):
    """Admin form to add whitelisted phone numbers for an account (single or bulk)."""
    user = forms.ModelChoiceField(
        queryset=None,
        label="Account / User",
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        empty_label="Select Account",
    )
    numbers = forms.CharField(
        label="Phone Numbers",
        widget=forms.Textarea(
            attrs={
                "class": TAILWIND_INPUT,
                "rows": 4,
                "placeholder": "Enter phone numbers separated by comma or new line\ne.g. 9876543210, 919876543211",
            }
        ),
        help_text="Enter priority numbers for this account. They will never be cut/skipped in this account's campaigns.",
    )
    description = forms.CharField(
        label="Remarks / Owner",
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. Director VIP, Test number"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import User
        self.fields["user"].queryset = User.objects.all().order_by("username")
        self.fields["user"].label_from_instance = lambda obj: (
            f"{obj.username} ({obj.company_name})" if obj.company_name else f"{obj.username} [{obj.role.title()}]"
        )



class ProfileSettingsForm(forms.ModelForm):
    """Form for users to update their company profile and contact details."""

    class Meta:
        from .models import User
        model = User
        fields = [
            "company_name",
            "first_name",
            "last_name",
            "email",
            "phone",
            "GSTIN",
            "state",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Acme Communications Pvt Ltd"}),
            "first_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "First Name"}),
            "last_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Last Name"}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "account@enterprise.com"}),
            "phone": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "+91 9876543210"}),
            "GSTIN": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "27AAPFU0939F1ZV"}),
            "state": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Maharashtra"}),
        }


class UserPasswordChangeForm(BasePasswordChangeForm):
    """Tailwind-styled password change form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({
                "class": TAILWIND_INPUT,
                "placeholder": "••••••••",
            })


TAILWIND_FILE = (
    "w-full text-xs text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl "
    "file:border-0 file:text-xs file:font-semibold file:bg-blue-50 file:text-blue-700 "
    "hover:file:bg-blue-100 file:cursor-pointer cursor-pointer border border-slate-200 rounded-xl p-1 bg-white"
)


class WhiteLabelForm(forms.ModelForm):
    """Admin-only form to configure white-label branding details."""

    class Meta:
        from .models import WhiteLabel
        model = WhiteLabel
        fields = [
            "company_name",
            "pointed_subdomain",
            "website_url",
            "support_number",
            "support_email",
            "logo",
            "square_logo",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Your Brand / Company Name"}),
            "pointed_subdomain": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "rcs.yourbrand.com or 152.58.188.93"}),
            "website_url": forms.URLInput(attrs={"class": TAILWIND_INPUT, "placeholder": "https://yourbrand.com"}),
            "support_number": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "+91 9876543210"}),
            "support_email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "support@yourbrand.com"}),
            "logo": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": "image/*"}),
            "square_logo": forms.FileInput(attrs={"class": TAILWIND_FILE, "accept": "image/*"}),
        }

    def clean_pointed_subdomain(self):
        val = self.cleaned_data.get("pointed_subdomain", "").strip().lower()
        val = val.replace("https://", "").replace("http://", "").split("/")[0].strip()
        if not val:
            raise forms.ValidationError("Please provide a valid pointed IP or sub-domain.")
        return val


class UserKYCForm(forms.ModelForm):
    """Form for users to enter and update their KYC details."""

    class Meta:
        from .models import UserKYC
        model = UserKYC
        fields = [
            "company_name",
            "person_name",
            "gst_number",
            "pan_card",
            "aadhar",
            "email",
            "phone",
            "company_website",
        ]
        widgets = {
            "company_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. Acme Technologies Pvt Ltd"}),
            "person_name": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. John Doe"}),
            "gst_number": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. 27ABCDE1234F1Z5 (Optional)"}),
            "pan_card": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. ABCDE1234F"}),
            "aadhar": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. 1234 5678 9012"}),
            "email": forms.EmailInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. contact@acme.com"}),
            "phone": forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. +91 9876543210"}),
            "company_website": forms.URLInput(attrs={"class": TAILWIND_INPUT, "placeholder": "e.g. https://www.acme.com (Optional)"}),
        }

    def clean_pan_card(self):
        val = self.cleaned_data.get("pan_card", "").strip().upper()
        if not val:
            raise forms.ValidationError("PAN card number is required.")
        return val

    def clean_gst_number(self):
        val = self.cleaned_data.get("gst_number", "").strip().upper()
        return val

    def clean_aadhar(self):
        val = self.cleaned_data.get("aadhar", "").strip()
        if not val:
            raise forms.ValidationError("Aadhar number is required.")
        return val


