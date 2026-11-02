"""Fast batch campaign dispatcher for urgent bulk dispatches.
Sends contacts to OneXtel in batches of 50-100 per HTTP call.
"""
import os
import sys
import django
from decimal import Decimal
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone
from campaigns.models import Campaign, CampaignContact
from messaging.models import Message
from messaging.tasks import build_onextel_rcs_item
from wallet.models import WalletTransaction, RatePlan
from wallet.services import credit, record_campaign_refund
from integrations.onextel.client import OneXtelClient
from reports.models import record_submission_and_cost, sync_user_daily_mis, sync_live_status


def fast_dispatch(campaign_id: str, batch_size: int = 50):
    try:
        campaign = Campaign.objects.select_related("user", "user__parent_reseller", "distributor", "sender_profile", "template").get(id=campaign_id)
    except Campaign.DoesNotExist:
        print(f"Error: Campaign {campaign_id} not found!")
        return

    user = campaign.user
    bot_api_key = (
        campaign.sender_profile.api_key.strip()
        if (campaign.sender_profile and campaign.sender_profile.api_key)
        else ""
    )
    if not bot_api_key:
        print(f"Error: Bot API key is missing for sender profile {campaign.sender_profile}")
        return

    client = OneXtelClient(api_key=bot_api_key)

    total_pending = CampaignContact.objects.filter(campaign=campaign, status=CampaignContact.Status.PENDING).count()
    print(f"Starting fast dispatch for '{campaign.name}' (ID: {campaign.id})")
    print(f"Total Pending Contacts: {total_pending} | Batch size: {batch_size}")

    campaign.status = Campaign.Status.RUNNING
    if not campaign.started_at:
        campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])

    processed_so_far = 0
    t_start = time.time()

    while True:
        pending_contacts = list(
            CampaignContact.objects.filter(
                campaign=campaign,
                status=CampaignContact.Status.PENDING,
            ).order_by("id")[:batch_size]
        )

        if not pending_contacts:
            break

        # Build message models
        messages_to_create = []
        contacts_to_update = []
        user_wallet = getattr(user, "wallet", None)
        transactions_to_create = []

        for contact in pending_contacts:
            msg = Message(
                campaign=campaign,
                user=user,
                distributor=campaign.distributor,
                sender_profile=campaign.sender_profile,
                message_type=campaign.message_type,
                recipient=contact.number,
                content_type=campaign.content_type,
                payload=campaign.custom_content if not campaign.template else {},
                template=campaign.template,
                custom_params=contact.params,
                rate_applied=campaign.rate_applied,
                cost=campaign.rate_applied,
                distributor_cost=campaign.distributor_rate_applied if campaign.distributor else Decimal("0.0000"),
                status=Message.Status.QUEUED,
            )
            messages_to_create.append(msg)

        # Bulk create messages
        created_msgs = Message.objects.bulk_create(messages_to_create)

        # Build OneXtel items
        rcs_items = []
        msg_map = {}
        for idx, contact in enumerate(pending_contacts):
            msg = created_msgs[idx]
            contact.status = CampaignContact.Status.DISPATCHED
            contact.message = msg
            contacts_to_update.append(contact)

            rcs_items.append(build_onextel_rcs_item(msg))
            msg_map[contact.number] = msg

            if user_wallet:
                transactions_to_create.append(
                    WalletTransaction(
                        wallet=user_wallet,
                        type=WalletTransaction.TransactionType.DEBIT,
                        amount=msg.cost,
                        balance_after=user_wallet.balance,
                        reference_type="MESSAGE_DISPATCH",
                        reference_id=str(msg.id),
                        remarks=f"Fast Campaign '{campaign.name}' - {contact.number}",
                    )
                )

        # Send batch to OneXtel in ONE single HTTP call!
        try:
            results = client.send_messages(rcs_items, prefix="+91")
            # Map carrier response message IDs
            if isinstance(results, list):
                for res in results:
                    if isinstance(res, dict):
                        recip = res.get("recipient")
                        mid = res.get("messageId")
                        if recip and mid and recip in msg_map:
                            msg_map[recip].onextel_message_id = mid
                            msg_map[recip].status = Message.Status.SUBMITTED
                            msg_map[recip].submitted_at = timezone.now()
            else:
                for msg in created_msgs:
                    msg.status = Message.Status.SUBMITTED
                    msg.submitted_at = timezone.now()
        except Exception as api_err:
            print(f"Warning: Batch send API error: {api_err}. Marking messages queued.")
            for msg in created_msgs:
                msg.failure_reason = str(api_err)[:250]

        # Bulk update messages and contacts
        Message.objects.bulk_update(created_msgs, ["onextel_message_id", "status", "submitted_at", "failure_reason"])
        CampaignContact.objects.bulk_update(contacts_to_update, ["status", "message"])
        if transactions_to_create:
            WalletTransaction.objects.bulk_create(transactions_to_create)

        # Deduct reserved amount
        batch_cost = sum((m.cost for m in created_msgs), Decimal("0.0000"))
        campaign.reserved_amount = max(Decimal("0.0000"), campaign.reserved_amount - batch_cost)
        processed_so_far += len(pending_contacts)
        campaign.processed_count = CampaignContact.objects.filter(
            campaign=campaign,
            status__in=[CampaignContact.Status.DISPATCHED, CampaignContact.Status.DELIVERED, CampaignContact.Status.FAILED],
        ).count()
        campaign.save(update_fields=["processed_count", "reserved_amount", "updated_at"])

        # Update metrics
        try:
            record_submission_and_cost(
                user_id=campaign.user_id,
                count=len(pending_contacts),
                cost=batch_cost,
                date=timezone.now().date(),
                timestamp=timezone.now(),
            )
        except Exception:
            pass

        pct = (processed_so_far / total_pending * 100) if total_pending else 100
        print(f"Dispatched {processed_so_far}/{total_pending} ({pct:.1f}%) in batch of {len(pending_contacts)}...")

    # Completion
    campaign.refresh_from_db()
    campaign.status = Campaign.Status.COMPLETED
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=["status", "completed_at", "updated_at"])

    try:
        c_date = campaign.created_at.date() if campaign.created_at else timezone.now().date()
        sync_user_daily_mis(user_id=campaign.user_id, date=c_date)
        sync_live_status(user_id=campaign.user_id, date=c_date)
    except Exception:
        pass

    elapsed = time.time() - t_start
    print(f"\nSUCCESS! Finished dispatching campaign '{campaign.name}' in {elapsed:.1f} seconds!")


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "1acd4fe0-0954-4c07-885b-a8ef0bd58249"
    fast_dispatch(cid)
