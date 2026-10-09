"""Dynamic per-client rate throttling configurable by admin per user."""
from rest_framework.throttling import UserRateThrottle


class ClientAPIRateThrottle(UserRateThrottle):
    """Throttle requests on per-client basis with admin-configured RPM overrides."""

    scope = "client_api"

    def get_rate(self):
        """Allow dynamic rate override from APIKey or User custom configuration."""
        if hasattr(self, "custom_rate") and self.custom_rate:
            return self.custom_rate
        return super().get_rate()

    def allow_request(self, request, view):
        """Extract per-user or per-key custom rate limit if configured."""
        if request.user and request.user.is_authenticated:
            api_key = getattr(request, "auth", None)
            custom_rpm = None

            if api_key and getattr(api_key, "rate_limit_rpm", None):
                custom_rpm = api_key.rate_limit_rpm
            elif getattr(request.user, "api_rate_limit_rpm", None):
                custom_rpm = request.user.api_rate_limit_rpm

            if custom_rpm:
                self.custom_rate = f"{custom_rpm}/min"
                self.num_requests, self.duration = self.parse_rate(self.custom_rate)
            else:
                self.custom_rate = None

        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"throttle_client_{request.user.id}"
        return self.get_ident(request)
