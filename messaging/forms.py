"""Forms for message logs filtering."""
from django import forms
from .models import Message



class MessageFilterForm(forms.Form):
    """Filters for searching and paginating message history."""

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Search recipient, msg ID, or carrier ID...",
            "class": "w-full pl-9 pr-3 py-1.5 bg-white border border-slate-200 rounded-xl text-xs font-medium text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs",
        }),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All Statuses")] + Message.Status.choices,
        widget=forms.Select(attrs={
            "class": "w-full px-3 py-1.5 bg-white border border-slate-200 rounded-xl text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs",
        }),
    )
    message_type = forms.ChoiceField(
        required=False,
        choices=[("", "All Intents")] + Message.MessageType.choices,
        widget=forms.Select(attrs={
            "class": "w-full px-3 py-1.5 bg-white border border-slate-200 rounded-xl text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs",
        }),
    )
    date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "w-full px-3 py-1.5 bg-white border border-slate-200 rounded-xl text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs",
        }),
    )
