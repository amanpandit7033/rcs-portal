"""API models for API keys, client webhook configuration, and delivery forward logs."""
import hashlib
import hmac
import secrets
from django.conf import settings
from django.db import models


class APIKey(models.Model):
    """Client API key with SHA-256 hashed secret storage and prefix indexing."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField("Key Name", max_length=100, default="Production API Key")
    prefix = models.CharField("Key Prefix", max_length=12, db_index=True)
    hashed_key = models.CharField("Hashed Secret", max_length=128)
    is_active = models.BooleanField("Is Active", default=True, db_index=True)
    last_used_at = models.DateTimeField("Last Used At", null=True, blank=True)
    ip_whitelist = models.JSONField("IP Whitelist", default=list, blank=True)
    rate_limit_rpm = models.PositiveIntegerField(
        "Custom Rate Limit (RPM)",
        null=True,
        blank=True,
        help_text="Custom requests per minute limit, configurable by admin per client",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"

    def __str__(self):
        return f"{self.name} ({self.prefix}...) - {self.user.email}"

    @classmethod
    def generate_raw_key(cls) -> str:
        """Generate a random key string starting with 'rcs_'."""
        return f"rcs_{secrets.token_urlsafe(32)}"

    @classmethod
    def hash_key(cls, raw_key: str) -> str:
        """Hash raw key using SHA-256."""
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def create_key(cls, user, name="Production API Key", ip_whitelist=None, rate_limit_rpm=None):
        """Create a new APIKey instance and return tuple of (APIKey, raw_key)."""
        raw_key = cls.generate_raw_key()
        prefix = raw_key[:10]
        hashed = cls.hash_key(raw_key)

        key_instance = cls.objects.create(
            user=user,
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            is_active=True,
            ip_whitelist=ip_whitelist or [],
            rate_limit_rpm=rate_limit_rpm,
        )
        return key_instance, raw_key

    def regenerate(self) -> str:
        """Regenerate secret for this key and return the new raw key (shown only once)."""
        raw_key = self.generate_raw_key()
        self.prefix = raw_key[:10]
        self.hashed_key = self.hash_key(raw_key)
        self.is_active = True
        self.save(update_fields=["prefix", "hashed_key", "is_active", "updated_at"])
        return raw_key

    def revoke(self):
        """Deactivate this API key immediately."""
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])

    def verify_key(self, raw_key: str) -> bool:
        """Constant-time verification of raw key against stored hash."""
        if not self.is_active:
            return False
        incoming_hash = self.hash_key(raw_key)
        return hmac.compare_digest(self.hashed_key, incoming_hash)

    def is_ip_allowed(self, ip_address: str) -> bool:
        """Check if incoming client IP is authorized under optional whitelist."""
        if not self.ip_whitelist:
            return True
        return ip_address in self.ip_whitelist


class ClientWebhook(models.Model):
    """User-configured external callback endpoint for receiving forwarded DLR receipts."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_webhook",
    )
    url = models.URLField("Callback URL", max_length=500)
    secret = models.CharField("HMAC Signing Secret", max_length=128, default=secrets.token_hex)
    is_active = models.BooleanField("Is Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client Webhook Config"
        verbose_name_plural = "Client Webhook Configs"

    def __str__(self):
        return f"{self.user.email} Webhook -> {self.url}"


class WebhookForwardLog(models.Model):
    """Historical audit of DLR callbacks forwarded to client webhook endpoints."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhook_forward_logs",
    )
    message_id = models.CharField("Message ID", max_length=50, db_index=True)
    url = models.URLField("Destination URL", max_length=500)
    payload = models.JSONField("Payload Forwarded", default=dict)
    status_code = models.IntegerField("HTTP Response Code", null=True, blank=True)
    response_body = models.TextField("Response Body", blank=True, default="")
    success = models.BooleanField("Success", default=False, db_index=True)
    attempt = models.PositiveIntegerField("Attempt Number", default=1)
    error_message = models.TextField("Error Message", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Webhook Forward Log"
        verbose_name_plural = "Webhook Forward Logs"
