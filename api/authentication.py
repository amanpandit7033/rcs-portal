"""Authentication backend for verifying client API keys with IP whitelisting."""
import logging
from django.utils import timezone
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed

from .models import APIKey

logger = logging.getLogger(__name__)


def get_request_client_ip(request) -> str:
    """Extract client remote IP addressing proxy forwarding."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "").strip()


class APIKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate requests presenting valid 'apikey' header."""

    def authenticate(self, request):
        raw_key = (
            request.headers.get("apikey")
            or request.headers.get("ApiKey")
            or request.headers.get("X-API-Key")
            or request.META.get("HTTP_APIKEY")
        )

        if not raw_key:
            # Check Authorization: Bearer <key> or ApiKey <key>
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                candidate = auth_header[7:].strip()
                if candidate.startswith("rcs_"):
                    raw_key = candidate
            elif auth_header.startswith("ApiKey "):
                raw_key = auth_header[7:].strip()

        if not raw_key:
            return None

        raw_key = str(raw_key).strip()
        prefix = raw_key[:10]

        # Fast lookup by prefix
        candidate_keys = APIKey.objects.select_related("user").filter(prefix=prefix, is_active=True)

        matching_key = None
        for key in candidate_keys:
            if key.verify_key(raw_key):
                matching_key = key
                break

        if not matching_key:
            raise AuthenticationFailed("Invalid or revoked API key.")

        # Check IP whitelist
        client_ip = get_request_client_ip(request)
        if not matching_key.is_ip_allowed(client_ip):
            logger.warning("Rejected API key %s from unlisted IP %s", matching_key.id, client_ip)
            raise AuthenticationFailed(f"Client IP '{client_ip}' is not permitted for this API key.")

        # Update last used timestamp
        APIKey.objects.filter(id=matching_key.id).update(last_used_at=timezone.now())

        return (matching_key.user, matching_key)

    def authenticate_header(self, request):
        return 'apikey realm="api"'


try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class APIKeyAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = "api.authentication.APIKeyAuthentication"
        name = "ApiKeyAuth"

        def get_security_definition(self, auto_schema):
            return {
                "type": "apiKey",
                "in": "header",
                "name": "apikey",
                "description": "Enter your active API key (starts with 'rcs_')",
            }
except ImportError:
    pass

