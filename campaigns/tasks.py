"""Celery tasks for chunked contact parsing, batch execution, and scheduled campaign dispatch."""
from decimal import Decimal
import logging
from celery import shared_task
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from messaging.models import Message
from messaging.services import normalize_and_validate_indian_phone
from messaging.tasks import send_message_task, throttle_rate_limit
from wallet.models import WalletTransaction
from wallet.services import release
from .models import Campaign, CampaignContact
from .services import calculate_campaign_estimate
from .utils import stream_file_records

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def parse_campaign_file_task(self, campaign_id: str, auto_launch: bool = False, schedule_datetime: str = None):
    """Parse uploaded CSV/XLSX in streaming chunks without loading full file into memory.

    Validates phone numbers, eliminates duplicate recipients, stores contacts, and updates totals.
    """
    try:
        campaign = Campaign.objects.select_related("user").get(id=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("parse_campaign_file_task: Campaign %s not found.", campaign_id)
        return

    campaign.status = Campaign.Status.PARSING
    campaign.error_message = ""
    campaign.save(update_fields=["status", "error_message", "updated_at"])

    file_path = campaign.file.path
    mapping = campaign.column_mapping or {}

    total_rows = 0
    valid_count = 0
    invalid_count = 0
    duplicate_count = 0

    seen_numbers = set()
    contacts_batch = []
    BATCH_SIZE = 1000

    try:
        for row_idx, raw_phone, params in stream_file_records(file_path, mapping):
            total_rows += 1

            # 1. Validation check
            clean_phone = ""
            val_error = ""
            try:
                clean_phone = normalize_and_validate_indian_phone(raw_phone)
            except ValidationError as e:
                val_error = str(e.messages[0] if hasattr(e, "messages") else e)

            if val_error:
                invalid_count += 1
                contacts_batch.append(
                    CampaignContact(
                        campaign=campaign,
                        number=raw_phone[:20] if raw_phone else f"Row-{row_idx}",
                        params=params,
                        status=CampaignContact.Status.INVALID,
                        error_message=val_error,
                    )
                )
            elif clean_phone in seen_numbers:
                duplicate_count += 1
                contacts_batch.append(
                    CampaignContact(
                        campaign=campaign,
                        number=clean_phone,
                        params=params,
                        status=CampaignContact.Status.DUPLICATE,
                        error_message="Duplicate recipient number in file",
                    )
                )
            else:
                valid_count += 1
                seen_numbers.add(clean_phone)
                contacts_batch.append(
                    CampaignContact(
                        campaign=campaign,
                        number=clean_phone,
                        params=params,
                        status=CampaignContact.Status.PENDING,
                    )
                )

            # Bulk save in batches to minimize memory and DB roundtrips
            if len(contacts_batch) >= BATCH_SIZE:
                CampaignContact.objects.bulk_create(contacts_batch)
                contacts_batch = []

        if contacts_batch:
            CampaignContact.objects.bulk_create(contacts_batch)

        # Update metrics and financial estimate
        campaign.total = total_rows
        campaign.valid_count = valid_count
        campaign.invalid_count = invalid_count
        campaign.duplicate_count = duplicate_count
        campaign.status = Campaign.Status.PARSED
        calculate_campaign_estimate(campaign)
        campaign.save(
            update_fields=[
                "total",
                "valid_count",
                "invalid_count",
                "duplicate_count",
                "status",
                "rate_applied",
                "estimated_cost",
                "updated_at",
            ]
        )
        logger.info(
            "Campaign %s parsed successfully: %d total, %d valid, %d invalid, %d duplicate",
            campaign.id,
            total_rows,
            valid_count,
            invalid_count,
            duplicate_count,
        )

        if auto_launch:
            if valid_count > 0:
                try:
                    from .services import reserve_campaign_funds
                    from wallet.services import InsufficientBalanceError

                    reserve_campaign_funds(campaign)

                    is_scheduled = False
                    if schedule_datetime:
                        from django.utils.dateparse import parse_datetime

                        dt = parse_datetime(schedule_datetime)
                        if dt and timezone.is_naive(dt):
                            dt = timezone.make_aware(dt, timezone.get_current_timezone())
                        if dt and dt > timezone.now():
                            campaign.scheduled_at = dt
                            campaign.status = Campaign.Status.SCHEDULED
                            campaign.save(update_fields=["scheduled_at", "status", "updated_at"])
                            is_scheduled = True

                    if not is_scheduled:
                        campaign.status = Campaign.Status.QUEUED
                        campaign.save(update_fields=["status", "updated_at"])
                        run_campaign_task.delay(str(campaign.id))
                        logger.info("Campaign %s auto-launched and queued.", campaign.id)
                except InsufficientBalanceError as e:
                    campaign.status = Campaign.Status.FAILED
                    campaign.error_message = f"Insufficient wallet balance: {e}"
                    campaign.save(update_fields=["status", "error_message", "updated_at"])
            else:
                campaign.status = Campaign.Status.FAILED
                campaign.error_message = "No valid recipient numbers found in input."
                campaign.save(update_fields=["status", "error_message", "updated_at"])

    except Exception as exc:
        logger.exception("Failed parsing campaign file for %s: %s", campaign.id, exc)
        campaign.status = Campaign.Status.FAILED
        campaign.error_message = f"File parsing error: {exc}"
        campaign.save(update_fields=["status", "error_message", "updated_at"])
        raise exc


@shared_task(bind=True, max_retries=1)
def run_campaign_task(self, campaign_id: str):
    """Batch-dispatch queued campaign contacts to the 'bulk' queue respecting throttle limits.

    Converts wallet reservation to per-message debits and releases unused reserve on completion.
    """
    try:
        campaign = Campaign.objects.select_related("user", "user__parent_reseller", "sender_profile", "template").get(id=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("run_campaign_task: Campaign %s not found.", campaign_id)
        return

    # Check valid launch state
    if campaign.status in [Campaign.Status.PAUSED, Campaign.Status.CANCELLED, Campaign.Status.COMPLETED]:
        logger.info("Campaign %s is in state %s. Halting runner task.", campaign.id, campaign.status)
        return

    campaign.status = Campaign.Status.RUNNING
    if not campaign.started_at:
        campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])

    # Low Price / Skimming Setup
    user = campaign.user
    total_valid_contacts = CampaignContact.objects.filter(
        campaign=campaign,
        status=CampaignContact.Status.PENDING,
    ).count()

    skipped_contact_ids = set()

    # Low Price rules:
    # 1. Enabled on user (is_low_price_applied = True)
    # 2. Campaigns with 20 or fewer numbers are automatically whitelisted (0 skipped)
    # 3. Campaign total >= low_price_start_from and percentage > 0
    if (
        user.is_low_price_applied
        and total_valid_contacts > 20
        and total_valid_contacts >= user.low_price_start_from
        and user.low_price_percentage > Decimal("0.00")
    ):
        from accounts.models import WhitelistNumber
        import random

        # 1. Fetch whitelisted priority numbers for this campaign's user (never cut)
        whitelisted_raw = list(
            WhitelistNumber.objects.filter(user=campaign.user).values_list("phone_number", flat=True)
        )
        whitelisted_set = set()
        for num in whitelisted_raw:
            cleaned = "".join(filter(str.isdigit, str(num)))
            if cleaned:
                whitelisted_set.add(cleaned)
                if len(cleaned) == 10:
                    whitelisted_set.add("91" + cleaned)
                elif cleaned.startswith("91") and len(cleaned) == 12:
                    whitelisted_set.add(cleaned[2:])

        # 2. Collect eligible contacts that are NOT whitelisted
        all_pending = list(
            CampaignContact.objects.filter(
                campaign=campaign,
                status=CampaignContact.Status.PENDING,
            ).values_list("id", "number")
        )

        eligible_ids = []
        for cid, num in all_pending:
            cleaned_num = "".join(filter(str.isdigit, str(num)))
            if cleaned_num not in whitelisted_set:
                eligible_ids.append(cid)

        # 3. Calculate target skip count = round(total_contacts * percentage / 100)
        target_skip_count = int(round(float(total_valid_contacts) * float(user.low_price_percentage) / 100.0))
        target_skip_count = min(target_skip_count, len(eligible_ids))

        if target_skip_count > 0:
            skipped_contact_ids = set(random.sample(eligible_ids, target_skip_count))
            logger.info(
                "Low Price cutting applied for campaign %s (user %s): skipping %s out of %s contacts (eligible: %s)",
                campaign.id, user.username, len(skipped_contact_ids), total_valid_contacts, len(eligible_ids)
            )

    BATCH_CHUNK = 200

    while True:
        # Re-check status from DB before each chunk to allow instant pause/cancel
        campaign.refresh_from_db(fields=["status", "reserved_amount"])
        if campaign.status in [Campaign.Status.PAUSED, Campaign.Status.CANCELLED]:
            logger.info("Campaign %s was %s. Gracefully stopping runner loop.", campaign.id, campaign.status)
            return

        pending_contacts = list(
            CampaignContact.objects.filter(
                campaign=campaign,
                status=CampaignContact.Status.PENDING,
            ).order_by("id")[:BATCH_CHUNK]
        )

        if not pending_contacts:
            # All contacts processed!
            break

        for contact in pending_contacts:
            throttle_rate_limit()

            is_skipped = contact.id in skipped_contact_ids

            # Create individual Message
            message = Message.objects.create(
                campaign=campaign,
                user=campaign.user,
                sender_profile=campaign.sender_profile,
                message_type=campaign.message_type,
                recipient=contact.number,
                content_type=campaign.content_type,
                payload=campaign.custom_content if not campaign.template else {},
                template=campaign.template,
                custom_params=contact.params,
                rate_applied=campaign.rate_applied,
                cost=campaign.rate_applied,
                status=Message.Status.SUBMITTED if is_skipped else Message.Status.QUEUED,
                submitted_at=timezone.now() if is_skipped else None,
                delivered_at=None,
                custref6="LOW_PRICE_SKIPPED" if is_skipped else "",
            )

            # Convert reservation portion into real Message DEBIT ledger row
            try:
                user_wallet = getattr(campaign.user, "wallet", None)
                if user_wallet:
                    WalletTransaction.objects.create(
                        wallet=user_wallet,
                        type=WalletTransaction.TransactionType.DEBIT,
                        amount=message.cost,
                        balance_after=user_wallet.balance,
                        reference_type="MESSAGE_DISPATCH",
                        reference_id=str(message.id),
                        remarks=f"Campaign '{campaign.name}' - {contact.number}",
                    )
            except Exception as e:
                logger.error("Error creating debit transaction for campaign message %s: %s", message.id, e)

            # Deduct from reserved balance tracker
            campaign.reserved_amount = max(Decimal("0.0000"), campaign.reserved_amount - message.cost)

            contact.status = CampaignContact.Status.DISPATCHED
            contact.message = message
            contact.save(update_fields=["status", "message"])

            if is_skipped:
                # Credit rebate to parent reseller based on their configured rate plan
                parent_reseller = getattr(campaign.user, "parent_reseller", None)
                if parent_reseller:
                    from wallet.models import RatePlan
                    from wallet.services import credit

                    reseller_plan = RatePlan.objects.filter(
                        user=parent_reseller,
                        message_type=campaign.message_type,
                    ).first()

                    rebate_amount = Decimal("0.0000")
                    if reseller_plan:
                        rebate_amount = reseller_plan.cost_rate if reseller_plan.cost_rate > Decimal("0.0000") else reseller_plan.rate
                    if rebate_amount <= Decimal("0.0000"):
                        rebate_amount = message.cost

                    try:
                        credit(
                            user=parent_reseller,
                            amount=rebate_amount,
                            remarks=f"Low Price Rebate: Campaign '{campaign.name}' ({contact.number})",
                            reference_type="LOW_PRICE_REBATE",
                            reference_id=str(message.id),
                        )
                    except Exception as e:
                        logger.error("Failed to credit low price rebate to reseller %s: %s", parent_reseller.username, e)
            else:
                # Queue on the 'bulk' queue to prevent starving priority transactional messages
                send_message_task.apply_async(args=[str(message.id)], queue="bulk")

        # Update processed counter for live progress bar
        campaign.processed_count = CampaignContact.objects.filter(
            campaign=campaign,
            status__in=[
                CampaignContact.Status.DISPATCHED,
                CampaignContact.Status.DELIVERED,
                CampaignContact.Status.FAILED,
            ],
        ).count()
        campaign.save(update_fields=["processed_count", "reserved_amount", "updated_at"])

    # Completed dispatch loop
    campaign.refresh_from_db()
    campaign.status = Campaign.Status.COMPLETED
    campaign.completed_at = timezone.now()

    # Release any unused reserve balance back to user
    unused_reserve = campaign.reserved_amount
    if unused_reserve > Decimal("0.0000"):
        try:
            release(
                user=campaign.user,
                amount=unused_reserve,
                remarks=f"Release unused reserved balance for completed campaign '{campaign.name}'",
                reference_type="CAMPAIGN_COMPLETE_RELEASE",
                reference_id=str(campaign.id),
            )
            logger.info("Released %s unused reserve for completed campaign %s", unused_reserve, campaign.id)
        except Exception as e:
            logger.error("Failed releasing unused reserve for completed campaign %s: %s", campaign.id, e)

    campaign.reserved_amount = Decimal("0.0000")
    campaign.save(update_fields=["status", "completed_at", "reserved_amount", "updated_at"])
    logger.info("Campaign %s completed successfully.", campaign.id)

    # Sync daily MIS billing and Live Status for this campaign's user
    try:
        from reports.models import sync_user_daily_mis, sync_live_status
        if campaign.user_id:
            c_date = campaign.created_at.date() if campaign.created_at else timezone.now().date()
            sync_user_daily_mis(user_id=campaign.user_id, date=c_date)
            sync_live_status(user_id=campaign.user_id, date=c_date)
    except Exception as e:
        logger.warning("Failed syncing MIS billing / Live Status on campaign completion: %s", e)


@shared_task
def check_scheduled_campaigns():
    """Periodic Celery Beat task scanning for scheduled campaigns ready for dispatch."""
    now = timezone.now()
    scheduled_campaigns = Campaign.objects.filter(
        status=Campaign.Status.SCHEDULED,
        scheduled_at__lte=now,
    )

    count = 0
    for campaign in scheduled_campaigns:
        campaign.status = Campaign.Status.QUEUED
        campaign.save(update_fields=["status", "updated_at"])
        run_campaign_task.delay(campaign.id)
        count += 1

    if count > 0:
        logger.info("check_scheduled_campaigns: Launched %d scheduled campaigns.", count)
    return count
