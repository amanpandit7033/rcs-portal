"""Accounts models."""
from decimal import Decimal
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from .managers import RoleScopedManager, ScopedUserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model supporting username-based authentication and multi-tier roles."""

    class Role(models.TextChoices):
        SUPERADMIN = "superadmin", "Superadmin"
        RESELLER = "reseller", "Reseller"
        DISTRIBUTOR = "distributor", "Distributor"
        USER = "user", "User"

    Role.ADMIN = Role.SUPERADMIN

    username = models.CharField(
        "Username",
        max_length=150,
        unique=True,
        db_index=True,
        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
    )
    email = models.EmailField("Email address", blank=True, null=True, db_index=True)
    first_name = models.CharField("First name", max_length=150, blank=True)
    last_name = models.CharField("Last name", max_length=150, blank=True)

    role = models.CharField(
        "Role",
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
        db_index=True,
    )
    parent_reseller = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients",
        limit_choices_to={"role__in": [Role.RESELLER, Role.DISTRIBUTOR]},
        help_text="Reseller or Distributor who manages this user account (if applicable)",
    )

    allow_refund = models.BooleanField(
        "Allow Refunds",
        default=True,
        help_text="Permit automated wallet refunds on message delivery failure",
    )
    can_view_live_status = models.BooleanField(
        "Allow Live Status Access",
        default=False,
        help_text="Permit this user/reseller to access the real-time Live Status report",
    )

    company_name = models.CharField("Company Name", max_length=255, blank=True)
    phone = models.CharField("Phone Number", max_length=20, blank=True)
    GSTIN = models.CharField("GSTIN", max_length=15, blank=True)
    state = models.CharField("State", max_length=100, blank=True)

    # Low Price / Cutting Configuration
    can_use_low_price = models.BooleanField(
        "Allow Low Price Feature",
        default=False,
        help_text="Permit this reseller to configure low price cutting for their clients",
    )
    is_low_price_applied = models.BooleanField(
        "Apply Low Price",
        default=False,
        help_text="Enable cutting / skimming on campaigns exceeding the threshold",
    )
    low_price_start_from = models.PositiveIntegerField(
        "Low Price Start From",
        default=0,
        help_text="Minimum campaign recipient count to trigger low price cutting",
    )
    low_price_percentage = models.DecimalField(
        "Low Price Percentage (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Percentage of contacts to skip/cut",
    )
    low_price_credit_rate = models.DecimalField(
        "Low Price Credit Amount (₹)",
        max_digits=8,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Amount credited to parent reseller per skipped message",
    )

    is_active = models.BooleanField("Active", default=True)
    is_staff = models.BooleanField("Staff status", default=False)
    date_joined = models.DateTimeField("Date joined", default=timezone.now)

    objects = ScopedUserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-date_joined"]

    def __str__(self):
        name = self.get_full_name()
        if name:
            return f"{name} (@{self.username})"
        return self.username

    def get_full_name(self):
        full = f"{self.first_name} {self.last_name}".strip()
        return full or self.company_name or self.username

    def get_short_name(self):
        return self.first_name or self.username

    @property
    def is_admin(self) -> bool:
        return self.is_superuser or self.role in [self.Role.SUPERADMIN, "admin"]

    @property
    def is_superadmin(self) -> bool:
        return self.is_superuser or self.role in [self.Role.SUPERADMIN, "admin"]

    @property
    def is_reseller(self) -> bool:
        return self.role == self.Role.RESELLER

    @property
    def is_distributor(self) -> bool:
        return self.role == self.Role.DISTRIBUTOR

    @property
    def is_regular_user(self) -> bool:
        return self.role == self.Role.USER

    def can_receive_refund(self) -> bool:
        """Hierarchical refund permission check.

        User gets refund only if they have allow_refund=True AND their managing parent has allow_refund=True.
        """
        if not self.allow_refund:
            return False
        if self.parent_reseller:
            return self.parent_reseller.can_receive_refund()
        return True

    def get_rates_dict(self):
        from decimal import Decimal
        rates = {rp.message_type: rp for rp in self.rate_plans.all()}
        return {
            "promotional_rate": str(rates.get("PROMOTIONAL").rate if rates.get("PROMOTIONAL") else Decimal("0.2500")),
            "transactional_rate": str(rates.get("TRANSACTIONAL").rate if rates.get("TRANSACTIONAL") else Decimal("0.2000")),
            "otp_rate": str(rates.get("OTP").rate if rates.get("OTP") else Decimal("0.1500")),
        }

    def get_sender_profiles_list(self):
        return [
            {
                "id": sp.id,
                "sender_id": sp.sender_id,
                "sender_profile_name": sp.sender_profile_name,
                "api_key": sp.api_key,
                "is_active": sp.is_active,
                "created_at": sp.created_at.strftime("%b %d, %Y") if sp.created_at else "",
            }
            for sp in self.sender_profiles.all()
        ]


class RoleScopedModel(models.Model):
    """Abstract base model for domain entities owned by a user.

    Automatically equips the model with a user ForeignKey and RoleScopedManager.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RoleScopedManager()

    class Meta:
        abstract = True


class WhiteLabel(models.Model):
    """White-label configuration managed exclusively by administrators."""
    company_name = models.CharField("Company Name", max_length=255)
    pointed_subdomain = models.CharField(
        "Pointed IP Sub-domain",
        max_length=255,
        unique=True,
        help_text="Pointed IP or Sub-domain (e.g. rcs.yourbrand.com or 152.58.188.93)",
    )
    website_url = models.URLField("Website URL", max_length=255, blank=True)
    support_number = models.CharField("Support Number", max_length=50, blank=True)
    support_email = models.EmailField("Support Mail ID", blank=True)
    logo = models.FileField(
        "Logo",
        upload_to="whitelabel/logos/",
        blank=True,
        null=True,
        help_text="Brand Logo image (PNG, JPG, SVG, WebP)",
    )
    square_logo = models.FileField(
        "Square Logo (1:1)",
        upload_to="whitelabel/logos/",
        blank=True,
        null=True,
        help_text="Square 1:1 ratio logo / app icon (PNG, JPG, SVG, WebP) shown on browser tab favicon & when sidebar menu is toggled/collapsed",
    )
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "White Label"
        verbose_name_plural = "White Labels"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company_name} ({self.pointed_subdomain})"


class WhitelistNumber(models.Model):
    """Priority whitelisted phone numbers that are never cut/skipped during campaign dispatch for a specific account."""
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="whitelisted_numbers",
        help_text="Account this whitelist belongs to",
    )
    phone_number = models.CharField("Phone Number", max_length=20, db_index=True)
    description = models.CharField("Remarks / Owner", max_length=255, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_whitelists",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Whitelisted Number"
        verbose_name_plural = "Whitelisted Numbers"
        ordering = ["-created_at"]
        unique_together = ("user", "phone_number")

    def __str__(self):
        return f"{self.user.username}: {self.phone_number} ({self.description})" if self.description else f"{self.user.username}: {self.phone_number}"


class UserKYC(models.Model):
    """KYC (Know Your Customer) details submitted by an account."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Verification"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="kyc",
    )
    company_name = models.CharField("Company Name", max_length=255)
    person_name = models.CharField("Person Name", max_length=150)
    gst_number = models.CharField("GST Number", max_length=20, blank=True)
    pan_card = models.CharField("PAN Card", max_length=20)
    aadhar = models.CharField("Aadhar", max_length=20)
    email = models.EmailField("Email", max_length=254)
    phone = models.CharField("Phone Number", max_length=20)
    company_website = models.URLField("Website of Company", max_length=255, blank=True)

    status = models.CharField(
        "KYC Status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    remarks = models.TextField("Remarks", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User KYC"
        verbose_name_plural = "User KYCs"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"KYC: {self.user.username} ({self.company_name}) - {self.get_status_display()}"


class LoginHistory(models.Model):
    """Logs user login activity across the portal."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="login_history",
    )
    ip_address = models.GenericIPAddressField("IP Address", null=True, blank=True)
    user_agent = models.CharField("User Agent", max_length=512, blank=True)
    device = models.CharField("Device / Browser", max_length=150, blank=True)
    timestamp = models.DateTimeField("Login Time", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Login History"
        verbose_name_plural = "Login Histories"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.user.username} @ {self.ip_address} ({self.timestamp:%Y-%m-%d %H:%M})"


