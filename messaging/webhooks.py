"""Webhook handlers for receiving external carrier delivery notifications (DLR)."""
import json
import logging
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import DLRLog
from .tasks import process_dlr_task

logger = logging.getLogger(__name__)


def get_client_ip(request) -> str:
    """Extract client IP handling reverse proxy headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "").strip()


@method_decorator(csrf_exempt, name="dispatch")
class OneXtelDLRWebhookView(View):
    """Public webhook receiver for OneXtel RCS delivery reports."""

    def post(self, request):
        # 1. IP Allowlist Verification (if configured)
        ip_allowlist = getattr(settings, "ONEXTEL_DLR_IP_ALLOWLIST", [])
        client_ip = get_client_ip(request)
        if ip_allowlist and client_ip not in ip_allowlist:
            logger.warning("Rejected DLR webhook from unlisted IP: %s", client_ip)
            return JsonResponse({"error": "Forbidden: IP not allowlisted"}, status=403)

        # 2. Shared Secret Token Authentication
        configured_secret = getattr(settings, "ONEXTEL_DLR_SECRET", "dev-dlr-secret-token")
        incoming_secret = (
            request.headers.get("X-Webhook-Secret")
            or request.headers.get("X-OneXtel-Secret")
            or request.GET.get("secret")
            or request.headers.get("Authorization", "").replace("Bearer ", "").strip()
        )

        if configured_secret and incoming_secret != configured_secret:
            logger.warning("Rejected DLR webhook: invalid secret token from IP %s", client_ip)
            return JsonResponse({"error": "Unauthorized: invalid secret token"}, status=401)

        # 3. Parse JSON Body
        try:
            if request.body:
                payload = json.loads(request.body.decode("utf-8"))
            else:
                payload = dict(request.POST.items())
        except Exception as e:
            logger.error("DLR webhook received unparseable payload: %s", e)
            return JsonResponse({"error": "Invalid payload format"}, status=400)

        # 4. Save Raw Payload in DLRLog Table
        dlr_log = DLRLog.objects.create(
            payload=payload if isinstance(payload, (dict, list)) else {"raw": payload},
            source_ip=client_ip,
            processed=False,
        )

        # 5. Dispatch Asynchronous Celery Processing Task
        process_dlr_task.delay(dlr_log.id)

        # 6. Return HTTP 200 Immediately to Vendor
        return JsonResponse({
            "status": "received",
            "dlr_log_id": dlr_log.id,
            "message": "Delivery report received successfully.",
        }, status=200)

    def get(self, request):
        """Some vendors send GET verification or health checks."""
        return JsonResponse({"status": "active", "service": "OneXtel RCS DLR Receiver"})
