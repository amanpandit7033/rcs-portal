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
        campaign = Campaign.objects.select_related("user", "sender_profile", "template").get(id=campaign_id)
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
                status=Message.Status.QUEUED,
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
