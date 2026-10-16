"""Dynamic CSRF and White-Label middleware."""
from urllib.parse import urlparse
from django.middleware.csrf import CsrfViewMiddleware, RejectRequest


class DynamicCsrfViewMiddleware(CsrfViewMiddleware):
    """
    Extends Django's CsrfViewMiddleware to dynamically trust domains
    registered in the WhiteLabel database table, eliminating the need
    to manually add every reseller domain to CSRF_TRUSTED_ORIGINS in .env.
    """

    def _is_whitelabel_domain(self, netloc_or_url):
        if not netloc_or_url:
            return False
        try:
            if "://" in netloc_or_url:
                netloc = urlparse(netloc_or_url).netloc
            else:
                netloc = netloc_or_url
            domain = netloc.split(":")[0].strip().lower()
            if not domain:
                return False

            from accounts.models import WhiteLabel
            return WhiteLabel.objects.filter(is_active=True, pointed_subdomain__iexact=domain).exists()
        except Exception:
            return False

    def _origin_verified(self, request):
        if super()._origin_verified(request):
            return True

        request_origin = request.META.get("HTTP_ORIGIN")
        if request_origin and self._is_whitelabel_domain(request_origin):
            return True

        return False

    def _check_referer(self, request):
        try:
            super()._check_referer(request)
        except RejectRequest:
            referer = request.META.get("HTTP_REFERER")
            if referer and self._is_whitelabel_domain(referer):
                return
            raise
