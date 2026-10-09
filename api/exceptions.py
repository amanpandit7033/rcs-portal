"""Consistent REST exception formatting with proper HTTP status codes (401, 402, 422/400, 429)."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
    ValidationError as DRFValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler

from wallet.services import InsufficientBalanceError


def custom_api_exception_handler(exc, context):
    """Normalize all API errors into standard format: {"error": {"code": ..., "message": ..., "details": ...}}."""

    # 1. Financial Insufficient Balance -> HTTP 402 Payment Required
    if isinstance(exc, InsufficientBalanceError):
        return Response(
            {
                "error": {
                    "code": "insufficient_balance",
                    "message": str(exc),
                    "details": {},
                }
            },
            status=402,
        )

    # 2. Django Core ValidationError -> HTTP 422 Unprocessable Entity
    if isinstance(exc, DjangoValidationError):
        messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
        return Response(
            {
                "error": {
                    "code": "validation_error",
                    "message": messages[0] if messages else "Validation failed",
                    "details": {"errors": messages},
                }
            },
            status=422,
        )

    # 3. Standard DRF handled exceptions
    response = exception_handler(exc, context)

    if response is not None:
        code = "api_error"

        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            code = "authentication_failed"
        elif isinstance(exc, PermissionDenied):
            code = "permission_denied"
        elif isinstance(exc, Throttled):
            code = "rate_limit_exceeded"
        elif isinstance(exc, DRFValidationError):
            code = "validation_error"

        message = "An error occurred processing your request."
        details = {}

        if isinstance(response.data, dict):
            if "detail" in response.data:
                message = str(response.data["detail"])
            else:
                details = response.data
                # Pick first error as top-level message
                first_key = next(iter(response.data), None)
                if first_key:
                    first_val = response.data[first_key]
                    if isinstance(first_val, list) and first_val:
                        message = f"{first_key}: {first_val[0]}"
                    else:
                        message = f"{first_key}: {first_val}"
        elif isinstance(response.data, list) and response.data:
            message = str(response.data[0])

        response.data = {
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        }

    return response
