"""Public REST API v1 endpoints for RCS messaging, templates, and wallet balance."""
from decimal import Decimal
import logging
import uuid
from django.contrib import messages as django_messages
from django.db import models, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views import View
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.mixins import RoleRequiredMixin
from messaging.models import Message
from messaging.services import submit_message
from templates_mgmt.models import Template
from wallet.models import SenderProfile
from wallet.utils import format_inr
from .authentication import APIKeyAuthentication
from .idempotency import IdempotencyMixin
from .models import APIKey, ClientWebhook
from .serializers import (
    BalanceSerializer,
    BulkMessageSendSerializer,
    MessageSendSerializer,
    SingleMessageResultSerializer,
    TemplateCreateSerializer,
    TemplateListSerializer,
)
from .throttling import ClientAPIRateThrottle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Message Dispatch Endpoints
# ---------------------------------------------------------------------------

class MessageSendAPIView(IdempotencyMixin, APIView):
    """Dispatch single message or batch array of up to 100 recipients."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]

    @extend_schema(
        summary="Send single or multi-recipient RCS message",
        description="Submit message for immediate dispatch. Supports single recipient or array of up to 100 recipients. Returned IDs are our UUIDs.",
        request=MessageSendSerializer,
        responses={
            201: SingleMessageResultSerializer(many=True),
            400: OpenApiResponse(description="Validation error"),
            402: OpenApiResponse(description="Insufficient wallet balance"),
            429: OpenApiResponse(description="Rate limit exceeded"),
        },
    )
    def post(self, request):
        return self.dispatch_idempotent(request, self._handle_send)

    def _handle_send(self, request):
        serializer = MessageSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user
        sender_profile_id = data["sender_profile_id"]
        message_type = data["message_type"]
        content_type = data["content_type"]
        payload = data.get("payload", {})
        template_id = data.get("template_id")
        custom_params = data.get("custom_params", {})
        ttl = data.get("ttl")
        custref1 = data.get("custref1", "")
        custref2 = data.get("custref2", "")
        custref3 = data.get("custref3", "")
        custref4 = data.get("custref4", "")
        custref5 = data.get("custref5", "")
        custref6 = data.get("custref6", "")

        # Collect target numbers
        recipients = data.get("recipients") or []
        if data.get("recipient"):
            recipients.insert(0, data["recipient"])

        created_messages = []

        # Atomic dispatch per batch
        with transaction.atomic():
            for num in recipients:
                msg = submit_message(
                    user=user,
                    sender_profile_id=sender_profile_id,
                    recipient=num,
                    message_type=message_type,
                    content_type=content_type,
                    payload=payload,
                    template_id=template_id,
                    custom_params=custom_params,
                    custref1=custref1,
                    custref2=custref2,
                    custref3=custref3,
                    custref4=custref4,
                    custref5=custref5,
                    custref6=custref6,
                    ttl=ttl,
                )
                created_messages.append(msg)

        out_serializer = SingleMessageResultSerializer(created_messages, many=True)
        resp_data = {
            "status": "queued",
            "count": len(created_messages),
            "messages": out_serializer.data,
        }
        return Response(resp_data, status=status.HTTP_201_CREATED)


class BulkMessageSendAPIView(IdempotencyMixin, APIView):
    """Batch message dispatch accepting array of recipients with per-contact variables."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]

    @extend_schema(
        summary="Submit bulk messages payload (up to 500 recipients)",
        request=BulkMessageSendSerializer,
        responses={201: SingleMessageResultSerializer(many=True), 402: OpenApiResponse(description="Insufficient balance")},
    )
    def post(self, request):
        return self.dispatch_idempotent(request, self._handle_bulk_send)

    def _handle_bulk_send(self, request):
        serializer = BulkMessageSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user
        sender_profile_id = data["sender_profile_id"]
        message_type = data["message_type"]
        template_id = data.get("template_id")
        content_type = data.get("content_type", Message.ContentType.TEMPLATE)
        payload = data.get("payload", {})
        message_items = data["messages"]

        created_messages = []

        with transaction.atomic():
            for item in message_items:
                msg = submit_message(
                    user=user,
                    sender_profile_id=sender_profile_id,
                    recipient=item["recipient"],
                    message_type=message_type,
                    content_type=content_type,
                    payload=payload,
                    template_id=template_id,
                    custom_params=item.get("custom_params", {}),
                    custref1=item.get("custref1", ""),
                )
                created_messages.append(msg)

        out_serializer = SingleMessageResultSerializer(created_messages, many=True)
        return Response(
            {
                "status": "queued",
                "count": len(created_messages),
                "messages": out_serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# 2. Message Status & Listing Endpoints
# ---------------------------------------------------------------------------

class MessageStatusAPIView(APIView):
    """Retrieve delivery status and timestamps for a specific message by our UUID."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]

    @extend_schema(
        summary="Get message delivery status by UUID",
        responses={200: SingleMessageResultSerializer, 404: OpenApiResponse(description="Message not found")},
    )
    def get(self, request, id):
        # Validate UUID
        try:
            val_uuid = uuid.UUID(str(id))
        except (ValueError, TypeError):
            return Response(
                {"error": {"code": "not_found", "message": f"Invalid message UUID '{id}'."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        message = get_object_or_404(Message.objects.for_user(request.user), id=val_uuid)
        serializer = SingleMessageResultSerializer(message)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MessageListAPIView(generics.ListAPIView):
    """Filterable and paginated list of dispatched messages scoped to authenticated user."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]
    serializer_class = SingleMessageResultSerializer

    @extend_schema(
        summary="List dispatched messages with filtering and pagination",
        parameters=[
            OpenApiParameter("status", str, description="Filter by status (queued, submitted, sent, delivered, read, failed)"),
            OpenApiParameter("message_type", str, description="Filter by message type (PROMOTIONAL, TRANSACTIONAL, OTP)"),
            OpenApiParameter("recipient", str, description="Filter by recipient phone number"),
            OpenApiParameter("date_from", str, description="Filter messages created on or after date (YYYY-MM-DD)"),
            OpenApiParameter("date_to", str, description="Filter messages created on or before date (YYYY-MM-DD)"),
        ],
    )
    def get_queryset(self):
        qs = Message.objects.for_user(self.request.user).order_by("-created_at")

        status_param = self.request.query_params.get("status")
        m_type = self.request.query_params.get("message_type")
        recipient = self.request.query_params.get("recipient")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if status_param:
            qs = qs.filter(status=status_param)
        if m_type:
            qs = qs.filter(message_type=m_type)
        if recipient:
            qs = qs.filter(recipient__icontains=recipient)
        if date_from:
            d = parse_date(date_from)
            if d:
                qs = qs.filter(created_at__date__gte=d)
        if date_to:
            d = parse_date(date_to)
            if d:
                qs = qs.filter(created_at__date__lte=d)

        return qs


# ---------------------------------------------------------------------------
# 3. Template Endpoints
# ---------------------------------------------------------------------------

class TemplateListCreateAPIView(generics.ListCreateAPIView):
    """List approved templates or submit a new template via API."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TemplateCreateSerializer
        return TemplateListSerializer

    @extend_schema(summary="List approved templates available for messaging")
    def get_queryset(self):
        return Template.objects.for_user(self.request.user).filter(
            status=Template.Status.APPROVED
        ).order_by("-created_at")

    @extend_schema(summary="Create a new template via API")
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class TemplateDetailAPIView(generics.RetrieveAPIView):
    """Retrieve details and detected placeholders of a specific template."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]
    serializer_class = TemplateListSerializer

    @extend_schema(summary="Get template details by ID")
    def get_queryset(self):
        return Template.objects.for_user(self.request.user)


# ---------------------------------------------------------------------------
# 4. Wallet Balance Endpoint
# ---------------------------------------------------------------------------

class BalanceAPIView(APIView):
    """Get current prepaid wallet balance in INR."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ClientAPIRateThrottle]

    @extend_schema(
        summary="Get current prepaid wallet balance",
        responses={200: BalanceSerializer},
    )
    def get(self, request):
        user = request.user
        wallet = getattr(user, "wallet", None)
        bal = wallet.balance if wallet else Decimal("0.0000")
        currency = wallet.currency if wallet else "INR"

        serializer = BalanceSerializer(
            {
                "balance": bal,
                "currency": currency,
                "formatted": format_inr(bal),
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# 5. Settings: API Keys & Client Webhook Management UI
# ---------------------------------------------------------------------------

class APISettingsView(RoleRequiredMixin, View):
    """User Settings screen to view, create, regenerate, and revoke API keys and configure webhooks."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "api/settings.html"

    def get(self, request):
        user = request.user
        api_keys = APIKey.objects.filter(user=user).order_by("-created_at")
        webhook, _ = ClientWebhook.objects.get_or_create(user=user)
        new_key_info = request.session.pop("newly_created_api_key", None)

        return render(
            request,
            self.template_name,
            {
                "api_keys": api_keys,
                "webhook": webhook,
                "new_key_info": new_key_info,
                "page_title": "API & Webhook Settings",
            },
        )

    def post(self, request):
        user = request.user
        action = request.POST.get("action")

        if action == "create_key":
            name = request.POST.get("name", "Production API Key").strip() or "Production API Key"
            ip_raw = request.POST.get("ip_whitelist", "").strip()
            ip_list = [ip.strip() for ip in ip_raw.split(",") if ip.strip()]

            key_instance, raw_key = APIKey.create_key(user=user, name=name, ip_whitelist=ip_list)
            # Store in session to display raw key ONCE
            request.session["newly_created_api_key"] = {
                "name": key_instance.name,
                "prefix": key_instance.prefix,
                "raw_key": raw_key,
            }
            django_messages.success(request, "New API key generated successfully. Copy it now, as it cannot be shown again!")

        elif action == "regenerate_key":
            key_id = request.POST.get("key_id")
            key_instance = get_object_or_404(APIKey, id=key_id, user=user)
            raw_key = key_instance.regenerate()
            request.session["newly_created_api_key"] = {
                "name": key_instance.name,
                "prefix": key_instance.prefix,
                "raw_key": raw_key,
            }
            django_messages.warning(request, f"API Key '{key_instance.name}' regenerated. Previous secret is revoked.")

        elif action == "revoke_key":
            key_id = request.POST.get("key_id")
            key_instance = get_object_or_404(APIKey, id=key_id, user=user)
            key_instance.revoke()
            django_messages.info(request, f"API Key '{key_instance.name}' has been revoked.")

        elif action == "save_webhook":
            url = request.POST.get("url", "").strip()
            webhook, _ = ClientWebhook.objects.get_or_create(user=user)
            webhook.url = url
            webhook.is_active = bool(request.POST.get("is_active"))
            webhook.save()
            django_messages.success(request, "Client webhook configuration saved successfully.")

        return redirect("api:settings")


@extend_schema(
    summary="Download Postman Collection",
    description="Export full Postman Collection JSON covering all public endpoints.",
    responses={200: dict},
)
class PostmanCollectionExportView(APIView):
    """Download Postman Collection JSON covering all public endpoints."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from .postman import get_postman_collection
        collection_data = get_postman_collection(request)
        return Response(collection_data, status=status.HTTP_200_OK, content_type="application/json")


class APIDocumentationView(View):
    """Comprehensive, standalone developer API reference & documentation page."""

    template_name = "api/documentation.html"

    def get(self, request):
        base_url = f"{request.scheme}://{request.get_host()}"
        user_keys = []
        user_webhook = None
        if request.user.is_authenticated:
            user_keys = APIKey.objects.filter(user=request.user, is_active=True).order_by("-created_at")
            user_webhook = ClientWebhook.objects.filter(user=request.user).first()

        return render(
            request,
            self.template_name,
            {
                "base_url": base_url,
                "user_keys": user_keys,
                "user_webhook": user_webhook,
                "page_title": "RCS Portal API Documentation & Reference",
            },
        )

