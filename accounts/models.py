"""Accounts models."""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from .managers import RoleScopedManager, ScopedUserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model supporting username-based authentication and multi-tier roles."""

    class Role(models.TextChoices):
        SUPERADMIN = "superadmin", "Superadmin"
        RESELLER = "reseller", "Reseller"
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
        limit_choices_to={"role": Role.RESELLER},
        help_text="Reseller who manages this user account (if applicable)",
    )

    company_name = models.CharField("Company Name", max_length=255, blank=True)
    phone = models.CharField("Phone Number", max_length=20, blank=True)
    GSTIN = models.CharField("GSTIN", max_length=15, blank=True)
    state = models.CharField("State", max_length=100, blank=True)

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
    def is_regular_user(self) -> bool:
        return self.role == self.Role.USER

    def get_rates_dict(self):
        from decimal import Decimal
        rates = {rp.message_type: rp for rp in self.rate_plans.all()}
        return {
            "promotional_rate": str(rates.get("PROMOTIONAL").rate if rates.get("PROMOTIONAL") else Decimal("0.2500")),
            "promotional_cost_rate": str(rates.get("PROMOTIONAL").cost_rate if rates.get("PROMOTIONAL") else Decimal("0.1800")),
            "transactional_rate": str(rates.get("TRANSACTIONAL").rate if rates.get("TRANSACTIONAL") else Decimal("0.2000")),
            "transactional_cost_rate": str(rates.get("TRANSACTIONAL").cost_rate if rates.get("TRANSACTIONAL") else Decimal("0.1500")),
            "otp_rate": str(rates.get("OTP").rate if rates.get("OTP") else Decimal("0.1500")),
            "otp_cost_rate": str(rates.get("OTP").cost_rate if rates.get("OTP") else Decimal("0.1000")),
        }

    def get_sender_profiles_list(self):
        return [
            {
                "id": sp.id,
                "sender_id": sp.sender_id,
                "sender_profile_name": sp.sender_profile_name,
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
