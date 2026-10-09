"""Wallet financial services and ledger management.

All balance modifications MUST occur exclusively through this service layer
within database transactions using select_for_update locking.
"""
from decimal import Decimal
from typing import Optional, Tuple, Union
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import RatePlan, Wallet, WalletTransaction


class WalletError(Exception):
    """Base exception for all wallet operations."""
    pass


class InsufficientBalanceError(WalletError):
    """Raised when an operation would cause wallet balance to drop below zero."""
    pass


class InvalidAmountError(WalletError):
    """Raised when a non-positive or invalid amount is provided."""
    pass


class RateValidationError(WalletError):
    """Raised when client rates violate reseller cost or hierarchy rules."""
    pass


def _to_decimal(amount: Union[Decimal, float, int, str]) -> Decimal:
    """Safely cast amount to Decimal with 4 decimal places."""
    try:
        dec = Decimal(str(amount))
    except Exception as e:
        raise InvalidAmountError(f"Invalid monetary value: {amount}") from e

    if dec <= Decimal("0.0000"):
        raise InvalidAmountError(f"Amount must be strictly greater than zero. Received: {dec}")

    # Quantize to 4 decimal places
    return dec.quantize(Decimal("0.0001"))


def get_locked_wallet(user) -> Wallet:
    """Acquire a row-level lock on the user's wallet within the current transaction."""
    wallet, _ = Wallet.objects.select_for_update().get_or_create(
        user=user,
        defaults={"balance": Decimal("0.0000"), "currency": "INR"},
    )
    return wallet


@transaction.atomic
def credit(
    user,
    amount: Union[Decimal, float, int, str],
    remarks: str = "",
    created_by=None,
    reference_type: str = "manual_credit",
    reference_id: str = "",
    transaction_type: str = WalletTransaction.TransactionType.CREDIT,
) -> WalletTransaction:
    """Add funds to a user's wallet and write an append-only ledger transaction."""
    dec_amount = _to_decimal(amount)
    wallet = get_locked_wallet(user)

    wallet.balance += dec_amount
    wallet.save(update_fields=["balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet,
        type=transaction_type,
        amount=dec_amount,
        balance_after=wallet.balance,
        reference_type=reference_type,
        reference_id=str(reference_id),
        remarks=remarks or f"Wallet credited with {dec_amount}",
        created_by=created_by,
    )


@transaction.atomic
def debit(
    user,
    amount: Union[Decimal, float, int, str],
    remarks: str = "",
    created_by=None,
    reference_type: str = "manual_debit",
    reference_id: str = "",
    transaction_type: str = WalletTransaction.TransactionType.DEBIT,
) -> WalletTransaction:
    """Deduct funds from a user's wallet. Prevents negative balances."""
    dec_amount = _to_decimal(amount)
    wallet = get_locked_wallet(user)

    if wallet.balance < dec_amount:
        raise InsufficientBalanceError(
            f"Insufficient wallet balance. Available: {wallet.balance}, Requested: {dec_amount}"
        )

    wallet.balance -= dec_amount
    wallet.save(update_fields=["balance", "updated_at"])

    return WalletTransaction.objects.create(
        wallet=wallet,
        type=transaction_type,
        amount=dec_amount,
        balance_after=wallet.balance,
        reference_type=reference_type,
        reference_id=str(reference_id),
        remarks=remarks or f"Wallet debited with {dec_amount}",
        created_by=created_by,
    )


@transaction.atomic
def refund(
    user,
    amount: Union[Decimal, float, int, str],
    remarks: str = "",
    created_by=None,
    reference_type: str = "message_refund",
    reference_id: str = "",
) -> WalletTransaction:
    """Refund funds back to the user's wallet with REFUND transaction type."""
    return credit(
        user=user,
        amount=amount,
        remarks=remarks or "Message refund credited",
        created_by=created_by,
        reference_type=reference_type,
        reference_id=reference_id,
        transaction_type=WalletTransaction.TransactionType.REFUND,
    )


@transaction.atomic
def reserve(
    user,
    amount: Union[Decimal, float, int, str],
    remarks: str = "",
    created_by=None,
    reference_type: str = "campaign_reservation",
    reference_id: str = "",
) -> WalletTransaction:
    """Reserve balance for an ongoing campaign dispatch."""
    return debit(
        user=user,
        amount=amount,
        remarks=remarks or "Balance reserved for campaign dispatch",
        created_by=created_by,
        reference_type=reference_type,
        reference_id=reference_id,
        transaction_type=WalletTransaction.TransactionType.RESERVATION,
    )


@transaction.atomic
def release(
    user,
    amount: Union[Decimal, float, int, str],
    remarks: str = "",
    created_by=None,
    reference_type: str = "campaign_release",
    reference_id: str = "",
) -> WalletTransaction:
    """Release unspent reserved balance back to user's available wallet."""
    return credit(
        user=user,
        amount=amount,
        remarks=remarks or "Unspent reserved balance released",
        created_by=created_by,
        reference_type=reference_type,
        reference_id=reference_id,
        transaction_type=WalletTransaction.TransactionType.RELEASE,
    )


@transaction.atomic
def transfer(
    from_user,
    to_user,
    amount: Union[Decimal, float, int, str],
    remarks: str = "",
    created_by=None,
) -> Tuple[WalletTransaction, WalletTransaction]:
    """Transfer funds between two accounts (e.g. Reseller to Client).

    Creates exactly TWO ledger entries:
    1. TRANSFER_OUT on the sender's wallet
    2. TRANSFER_IN on the recipient's wallet
    Deadlock-safe: acquires row locks ordered strictly by user ID.
    """
    if from_user.id == to_user.id:
        raise WalletError("Cannot transfer funds to the same account.")

    dec_amount = _to_decimal(amount)

    # Sort user IDs to guarantee consistent lock ordering across concurrent threads
    first_id, second_id = sorted([from_user.id, to_user.id])
    first_wallet = Wallet.objects.select_for_update().get_or_create(user_id=first_id)[0]
    second_wallet = Wallet.objects.select_for_update().get_or_create(user_id=second_id)[0]

    from_wallet = first_wallet if from_user.id == first_id else second_wallet
    to_wallet = second_wallet if to_user.id == second_id else first_wallet

    if from_wallet.balance < dec_amount:
        raise InsufficientBalanceError(
            f"Sender has insufficient balance ({from_wallet.balance}) for transfer of {dec_amount}."
        )

    # Adjust balances atomically
    from_wallet.balance -= dec_amount
    from_wallet.save(update_fields=["balance", "updated_at"])

    to_wallet.balance += dec_amount
    to_wallet.save(update_fields=["balance", "updated_at"])

    ref_id = f"XFER-{from_user.id}->{to_user.id}-{int(from_wallet.updated_at.timestamp())}"

    out_tx = WalletTransaction.objects.create(
        wallet=from_wallet,
        type=WalletTransaction.TransactionType.TRANSFER_OUT,
        amount=dec_amount,
        balance_after=from_wallet.balance,
        reference_type="wallet_transfer",
        reference_id=ref_id,
        remarks=remarks or f"Transfer out to {to_user.email}",
        created_by=created_by,
    )

    in_tx = WalletTransaction.objects.create(
        wallet=to_wallet,
        type=WalletTransaction.TransactionType.TRANSFER_IN,
        amount=dec_amount,
        balance_after=to_wallet.balance,
        reference_type="wallet_transfer",
        reference_id=ref_id,
        remarks=remarks or f"Transfer in from {from_user.email}",
        created_by=created_by,
    )

    return out_tx, in_tx


def validate_client_rate(client, message_type: str, client_rate: Union[Decimal, str, float]) -> None:
    """Validate that a client's rate is not lower than their parent reseller's rate."""
    dec_client_rate = _to_decimal(client_rate)

    parent_reseller = getattr(client, "parent_reseller", None)
    if not parent_reseller:
        return

    reseller_rate_plan = RatePlan.objects.filter(
        user=parent_reseller,
        message_type=message_type,
    ).first()

    if reseller_rate_plan:
        reseller_rate = reseller_rate_plan.rate
        if dec_client_rate < reseller_rate:
            raise RateValidationError(
                f"Client rate ({dec_client_rate}) cannot be lower than reseller rate ({reseller_rate}) "
                f"for message type '{message_type}'."
            )


@transaction.atomic
def set_user_rate(
    user,
    message_type: str,
    rate: Union[Decimal, str, float],
    cost_rate: Union[Decimal, str, float] = Decimal("0.0000"),
) -> RatePlan:
    """Configure or update a user's rate plan with hierarchy validation."""
    dec_rate = _to_decimal(rate)
    dec_cost_rate = _to_decimal(cost_rate) if cost_rate else Decimal("0.0000")

    # If user has a parent reseller, validate client rate is >= reseller's rate
    validate_client_rate(user, message_type, dec_rate)

    rate_plan, _ = RatePlan.objects.update_or_create(
        user=user,
        message_type=message_type,
        defaults={
            "rate": dec_rate,
            "cost_rate": dec_cost_rate,
        },
    )
    return rate_plan
