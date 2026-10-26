"""Business services for bulk campaign life cycle, wallet reservation, and state transitions."""
from decimal import Decimal
import logging
from typing import Optional
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from messaging.services import get_user_rate_for_type
from wallet.services import release, reserve, InsufficientBalanceError
from .models import Campaign, CampaignContact

logger = logging.getLogger(__name__)


def calculate_campaign_estimate(campaign: Campaign) -> Decimal:
    """Compute estimated cost based on valid contacts count and user rate for message type."""
    rate = get_user_rate_for_type(campaign.user, campaign.message_type)
    campaign.rate_applied = rate
    campaign.estimated_cost = Decimal(campaign.valid_count) * rate
    campaign.save(update_fields=["rate_applied", "estimated_cost"])
    return campaign.estimated_cost


@transaction.atomic
def reserve_campaign_funds(campaign: Campaign) -> Decimal:
    """Verify wallet balance and reserve full estimated amount before launch.

    Raises:
        InsufficientBalanceError if user cannot cover estimated campaign cost.
    """
    calculate_campaign_estimate(campaign)
    amount = campaign.estimated_cost

    if amount <= Decimal("0.0000"):
        return Decimal("0.0000")

    # Guard: check if already reserved
    if campaign.reserved_amount > Decimal("0.0000"):
        logger.info("Campaign %s already has reserved amount %s", campaign.id, campaign.reserved_amount)
        return campaign.reserved_amount

    # Debit user wallet with RESERVATION transaction type
    reserve(
        user=campaign.user,
        amount=amount,
        remarks=f"Balance reservation for campaign '{campaign.name}' ({campaign.valid_count} messages)",
        reference_type="CAMPAIGN_RESERVATION",
        reference_id=str(campaign.id),
    )

    campaign.reserved_amount = amount
    campaign.save(update_fields=["reserved_amount"])
    logger.info("Reserved %s INR for campaign %s", amount, campaign.id)
    return amount


@transaction.atomic
def pause_campaign(campaign: Campaign) -> bool:
    """Pause an active or queued campaign."""
    if campaign.status in [Campaign.Status.RUNNING, Campaign.Status.QUEUED]:
        campaign.status = Campaign.Status.PAUSED
        campaign.save(update_fields=["status", "updated_at"])
        logger.info("Campaign %s paused by user.", campaign.id)
        return True
    return False


@transaction.atomic
def resume_campaign(campaign: Campaign) -> bool:
    """Resume a paused campaign and queue Celery runner task."""
    from .tasks import run_campaign_task

    if campaign.status == Campaign.Status.PAUSED:
        campaign.status = Campaign.Status.RUNNING
        campaign.save(update_fields=["status", "updated_at"])
        run_campaign_task.delay(campaign.id)
        logger.info("Campaign %s resumed by user.", campaign.id)
        return True
    return False


@transaction.atomic
def cancel_campaign(campaign: Campaign) -> bool:
    """Cancel a campaign, mark remaining contacts cancelled, and release unused reservation."""
    if campaign.status in [Campaign.Status.COMPLETED, Campaign.Status.CANCELLED]:
        return False

    old_status = campaign.status
    campaign.status = Campaign.Status.CANCELLED
    campaign.completed_at = timezone.now()

    # Cancel pending contacts
    CampaignContact.objects.filter(
        campaign=campaign,
        status__in=[CampaignContact.Status.PENDING, CampaignContact.Status.QUEUED],
    ).update(status=CampaignContact.Status.CANCELLED)

    # Release unspent reservation back to user
    unused_reserve = campaign.reserved_amount
    if unused_reserve > Decimal("0.0000"):
        try:
            release(
                user=campaign.user,
                amount=unused_reserve,
                remarks=f"Release unspent reserved balance for cancelled campaign '{campaign.name}'",
                reference_type="CAMPAIGN_CANCEL_RELEASE",
                reference_id=str(campaign.id),
            )
            logger.info("Released %s INR unused reserve for cancelled campaign %s", unused_reserve, campaign.id)
        except Exception as e:
            logger.error("Failed releasing reserve for cancelled campaign %s: %s", campaign.id, e)

    campaign.reserved_amount = Decimal("0.0000")
    campaign.save(update_fields=["status", "completed_at", "reserved_amount", "updated_at"])
    return True


def auto_whitelist_campaign_recipients(campaign: Campaign) -> int:
    """Automatically adds recipients to user's priority WhitelistNumber table
    when a campaign created from the Campaign section has 20 or fewer recipients.

    Ensures that small test batches (<= 20 recipients) are permanently protected
    for that account and never subject to skimming or cuts in future dispatches.
    API submissions are strictly excluded from auto-whitelisting.
    """
    from accounts.models import WhitelistNumber

    valid_contacts = list(
        CampaignContact.objects.filter(campaign=campaign)
        .exclude(status__in=[CampaignContact.Status.INVALID, CampaignContact.Status.DUPLICATE])
        .values_list("number", flat=True)
    )

    if not (0 < len(valid_contacts) <= 20):
        return 0

    user = campaign.user
    # Fetch existing numbers for this user to avoid redundant writes
    existing_raw = set(
        WhitelistNumber.objects.filter(user=user).values_list("phone_number", flat=True)
    )
    existing_last10 = {
        "".join(filter(str.isdigit, str(n)))[-10:]
        for n in existing_raw
        if len("".join(filter(str.isdigit, str(n)))) >= 10
    }

    new_objects = []
    seen_in_batch = set()

    for raw_num in valid_contacts:
        digits = "".join(filter(str.isdigit, str(raw_num)))
        if not digits:
            continue
        last10 = digits[-10:] if len(digits) >= 10 else digits
        if last10 in existing_last10 or last10 in seen_in_batch:
            continue

        seen_in_batch.add(last10)
        formatted_num = f"+91{last10}" if len(last10) == 10 else f"+{digits}"

        new_objects.append(
            WhitelistNumber(
                user=user,
                phone_number=formatted_num,
                description=f"Auto-whitelisted (Campaign: {campaign.name[:45]})",
                created_by=user,
            )
        )

    if new_objects:
        WhitelistNumber.objects.bulk_create(new_objects, ignore_conflicts=True)
        logger.info(
            "Auto-whitelisted %d numbers for user %s from campaign %s (%s)",
            len(new_objects),
            user.username,
            campaign.id,
            campaign.name,
        )
        return len(new_objects)

    return 0

