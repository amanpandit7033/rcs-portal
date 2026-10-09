"""Forms for RCS Template creation, filtering, and media upload."""
from django import forms
from django.contrib.auth import get_user_model
from wallet.models import SenderProfile
from .models import MediaFile, Template

User = get_user_model()

TAILWIND_INPUT = (
    "w-full px-4 py-2.5 rounded-lg border border-slate-700 bg-slate-800/80 text-white "
    "placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all text-sm"
)
TAILWIND_SELECT = (
    "w-full px-4 py-2.5 rounded-lg border border-slate-700 bg-slate-800 text-white "
    "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition-all text-sm"
)


class TemplateFilterForm(forms.Form):
    """Filter form for templates list."""

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": TAILWIND_INPUT, "placeholder": "Search by template name..."}),
        label="Search",
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All Statuses")] + list(Template.Status.choices),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Status",
    )
    template_type = forms.ChoiceField(
        required=False,
        choices=[("", "All Types")] + list(Template.TemplateType.choices),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Template Type",
    )
    message_type = forms.ChoiceField(
        required=False,
        choices=[("", "All Categories")] + list(Template.MessageType.choices),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Message Type",
    )
    owner = forms.ModelChoiceField(
        required=False,
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": TAILWIND_SELECT}),
        label="Owner",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and (user.is_admin or user.is_superuser):
            self.fields["owner"].queryset = User.objects.all().order_by("email")
            self.fields["owner"].label_from_instance = lambda obj: f"{obj.company_name or obj.email} ({obj.email})"
        else:
            self.fields.pop("owner", None)


class MediaUploadForm(forms.ModelForm):
    """Media upload form."""

    class Meta:
        model = MediaFile
        fields = ["file"]
        widgets = {
            "file": forms.FileInput(attrs={"class": "text-sm text-slate-300 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500 cursor-pointer"}),
        }
