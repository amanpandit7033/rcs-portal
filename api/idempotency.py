"""Idempotency-Key header support to prevent duplicate message dispatches."""
from django.core.cache import cache
from rest_framework.response import Response


def get_idempotency_key(request) -> str:
    """Extract idempotency key from request headers."""
    return (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or ""
    ).strip()


class IdempotencyMixin:
    """Mixin for API views supporting Idempotency-Key caching."""

    def dispatch_idempotent(self, request, handler_fn, *args, **kwargs):
        idempotency_key = get_idempotency_key(request)

        if not idempotency_key or not getattr(request, "user", None) or not request.user.is_authenticated:
            return handler_fn(request, *args, **kwargs)

        cache_key = f"idempotency_{request.user.id}_{idempotency_key}"
        cached_result = cache.get(cache_key)

        if cached_result:
            data, status_code = cached_result
            response = Response(data, status=status_code)
            response["X-Idempotency-Status"] = "HIT"
            return response

        # Execute view logic
        response = handler_fn(request, *args, **kwargs)

        # Cache successful response for 24 hours
        if 200 <= response.status_code < 300:
            cache.set(cache_key, (response.data, response.status_code), timeout=86400)
            response["X-Idempotency-Status"] = "STORED"

        return response
