"""Root URL Configuration for RCS Portal."""
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def root_redirect(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard_redirect")
    return redirect("accounts:login")


def favicon_view(request):
    """Direct route for /favicon.ico matching domain white-label branding."""
    from accounts.models import WhiteLabel
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
    if wl and wl.square_logo:
        return redirect(wl.square_logo.url)
    if wl and wl.logo:
        return redirect(wl.logo.url)
    return redirect(f"{settings.STATIC_URL}images/rcs_logo.svg")


from django.conf import settings
from django.conf.urls.static import static

from api.views import APIDocumentationView
from messaging.webhooks import OneXtelDLRWebhookView

urlpatterns = [
    path("favicon.ico", favicon_view, name="favicon"),
    path("admin/", admin.site.urls),
    path("", root_redirect, name="root"),
    path("docs/", APIDocumentationView.as_view(), name="docs"),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("wallet/", include("wallet.urls", namespace="wallet")),
    path("templates/", include("templates_mgmt.urls", namespace="templates_mgmt")),
    path("messaging/", include("messaging.urls", namespace="messaging")),
    path("campaigns/", include("campaigns.urls", namespace="campaigns")),
    path("reports/", include("reports.urls", namespace="reports")),
    path("onboarding/", include("onboarding.urls", namespace="onboarding")),

    # Webhook Endpoints
    path("webhooks/onextel/dlr/", OneXtelDLRWebhookView.as_view(), name="onextel_dlr_webhook"),

    # Developer API & Settings
    path("api/", include("api.urls", namespace="api")),

    # API Documentation & Schema
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
