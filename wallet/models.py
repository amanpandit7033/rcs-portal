"""Wallet, Ledger Transactions, Rate Plans, and Sender Profile models."""
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone
from .utils import format_inr


class Wallet(models.Model):
    """Prepaid INR account balance for each user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
    )
    balance = models.DecimalField(
        "Wallet Balance (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
    )
    currency = models.CharField(
        "Currency",
        max_length=10,
        default="INR",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Wallet"
        verbose_name_plural = "Wallets"

    def __str__(self):
        return f"{self.user.email} - {self.formatted_balance}"

    @property
    def formatted_balance(self) -> str:
        return format_inr(self.balance, decimal_places=2)


class WalletTransaction(models.Model):
    """Append-only financial ledger tracking all balance adjustments."""

    class TransactionType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"
        TRANSFER_IN = "transfer_in", "Transfer In"
        TRANSFER_OUT = "transfer_out", "Transfer Out"
        RESERVATION = "reservation", "Reservation"
        RELEASE = "release", "Release"

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    type = models.CharField(
        "Transaction Type",
        max_length=20,
        choices=TransactionType.choices,
        db_index=True,
    )
    amount = models.DecimalField(
        "Amount (INR)",
        max_digits=14,
        decimal_places=4,
    )
    balance_after = models.DecimalField(
        "Balance After (INR)",
        max_digits=14,
        decimal_places=4,
    )
    reference_type = models.CharField(
        "Reference Type",
        max_length=50,
        blank=True,
        help_text="E.g. campaign, message, transfer, manual_adjustment",
    )
    reference_id = models.CharField(
        "Reference ID",
        max_length=100,
        blank=True,
        help_text="ID of the associated entity",
    )
    remarks = models.TextField("Remarks", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_wallet_transactions",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Wallet Transaction"
        verbose_name_plural = "Wallet Transactions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()} of {format_inr(self.amount)} ({self.wallet.user.email})"

    @property
    def formatted_amount(self) -> str:
        return format_inr(self.amount, decimal_places=2)

    @property
    def formatted_balance_after(self) -> str:
        return format_inr(self.balance_after, decimal_places=2)

    def save(self, *args, **kwargs):
        """Strict append-only constraint: updates to existing rows are disallowed."""
        if self.pk:
            raise PermissionDenied("Wallet transactions are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Strict append-only constraint: deleting ledger rows is disallowed."""
        raise PermissionDenied("Wallet transactions are immutable and cannot be deleted.")


class RatePlan(models.Model):
    """RCS messaging rates per message type for users/clients and cost rates for resellers."""

    class MessageType(models.TextChoices):
        PROMOTIONAL = "PROMOTIONAL", "Promotional"
        TRANSACTIONAL = "TRANSACTIONAL", "Transactional"
        OTP = "OTP", "OTP"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rate_plans",
    )
    message_type = models.CharField(
        "Message Type",
        max_length=20,
        choices=MessageType.choices,
    )
    rate = models.DecimalField(
        "Rate per Message (INR)",
        max_digits=14,
        decimal_places=4,
        help_text="Customer selling price per message",
    )
    cost_rate = models.DecimalField(
        "Cost Rate (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Underlying cost rate for margin computation",
    )
    valid_from = models.DateTimeField("Valid From", default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rate Plan"
        verbose_name_plural = "Rate Plans"
        unique_together = ("user", "message_type")
        ordering = ["message_type"]

    def __str__(self):
        return f"{self.user.email} - {self.get_message_type_display()}: {format_inr(self.rate, decimal_places=4)}"

    @property
    def formatted_rate(self) -> str:
        return format_inr(self.rate, decimal_places=4)

    @property
    def formatted_cost_rate(self) -> str:
        return format_inr(self.cost_rate, decimal_places=4)


class SenderProfile(models.Model):
    """Approved RCS Sender / Bot Profile manually assigned to users by Admin."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sender_profiles",
    )
    sender_id = models.CharField(
        "Sender / Bot ID",
        max_length=100,
        help_text="RCS Bot / Sender ID configured on carrier / vendor platform",
    )
    sender_profile_name = models.CharField(
        "Sender Profile Name",
        max_length=255,
        help_text="Display brand name shown on customer handsets",
    )
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Sender Profile"
        verbose_name_plural = "Sender Profiles"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.sender_profile_name} ({self.sender_id}) - {self.user.email}"
