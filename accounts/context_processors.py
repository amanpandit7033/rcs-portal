"""Context processor for white-label branding."""
from .models import WhiteLabel


def whitelabel_context(request):
    """Provide white-label brand configuration to templates.

    Matches by the requested host/subdomain first; fallbacks to the first active WhiteLabel record.
    """
    try:
        raw_host = request.META.get("HTTP_HOST", "")
        if not raw_host:
            try:
                raw_host = request.get_host()
            except Exception:
                raw_host = ""
        host = raw_host.split(":")[0].strip().lower()

        wl = None
        if host:
            wl = WhiteLabel.objects.filter(is_active=True, pointed_subdomain__iexact=host).first()
        if not wl:
            wl = WhiteLabel.objects.filter(is_active=True).first()
        return {"whitelabel": wl}
    except Exception:
        try:
            return {"whitelabel": WhiteLabel.objects.filter(is_active=True).first()}
        except Exception:
            return {"whitelabel": None}
