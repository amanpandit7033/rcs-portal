"""Fast batch campaign dispatcher for urgent bulk dispatches.
Sends messages to OneXtel in batches of 50-100 per HTTP call.
Handles both existing QUEUED messages and PENDING contacts.
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

    queued_count = Message.objects.filter(campaign=campaign, status=Message.Status.QUEUED).count()
    pending_contacts_count = CampaignContact.objects.filter(campaign=campaign, status=CampaignContact.Status.PENDING).count()

    print(f"==================================================")
    print(f"Starting Fast Dispatch for: '{campaign.name}' ({campaign.id})")
    print(f"Queued Messages waiting to send to Carrier: {queued_count}")
    print(f"Pending Contacts waiting to generate: {pending_contacts_count}")
    print(f"Batch Size: {batch_size} messages per HTTP request")
    print(f"==================================================")

    campaign.status = Campaign.Status.RUNNING
    if not campaign.started_at:
        campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])

    t_start = time.time()
    dispatched_now = 0

    # ---------------------------------------------------------
    # PART 1: Dispatch already-generated QUEUED messages
    # ---------------------------------------------------------
    if queued_count > 0:
        print(f"\n[PART 1] Sending {queued_count} QUEUED messages directly to OneXtel in batches of {batch_size}...")
        while True:
            batch_msgs = list(
                Message.objects.filter(
                    campaign=campaign,
                    status=Message.Status.QUEUED,
                ).order_by("id")[:batch_size]
            )

            if not batch_msgs:
                break

            rcs_items = []
            msg_map = {}
            for msg in batch_msgs:
                rcs_items.append(build_onextel_rcs_item(msg))
                clean_digits = "".join(filter(str.isdigit, str(msg.recipient)))
                msg_map[str(msg.recipient)] = msg
                msg_map[clean_digits] = msg
                if len(clean_digits) >= 10:
                    msg_map[clean_digits[-10:]] = msg

            try:
                results = client.send_messages(rcs_items, prefix="+91")
                raw_list = results
                if isinstance(results, dict):
                    if "data" in results and isinstance(results["data"], list):
                        raw_list = results["data"]
                    elif "result" in results and isinstance(results["result"], list):
                        raw_list = results["result"]

                if isinstance(raw_list, list):
                    for idx, res in enumerate(raw_list):
                        if isinstance(res, dict):
                            recip = str(res.get("recipient") or res.get("number") or "").strip()
                            mid = (
                                res.get("messageId")
                                or res.get("message_id")
                                or res.get("msgId")
                                or res.get("id")
                                or res.get("refId")
                                or ""
                            )
                            matched_msg = None
                            if recip:
                                r_digits = "".join(filter(str.isdigit, recip))
                                matched_msg = msg_map.get(recip) or msg_map.get(r_digits) or (msg_map.get(r_digits[-10:]) if len(r_digits) >= 10 else None)
                            if not matched_msg and idx < len(batch_msgs):
                                matched_msg = batch_msgs[idx]

                            if matched_msg:
                                matched_msg.onextel_message_id = mid
                                matched_msg.status = Message.Status.SUBMITTED
                                matched_msg.submitted_at = timezone.now()
                elif isinstance(results, dict):
                    mid = results.get("messageId") or results.get("msgId") or ""
                    for msg in batch_msgs:
                        msg.onextel_message_id = mid
                        msg.status = Message.Status.SUBMITTED
                        msg.submitted_at = timezone.now()
                else:
                    for msg in batch_msgs:
                        msg.status = Message.Status.SUBMITTED
                        msg.submitted_at = timezone.now()
            except Exception as api_err:
                print(f"Warning: Batch API error: {api_err}")
                for msg in batch_msgs:
                    msg.failure_reason = str(api_err)[:250]

            Message.objects.bulk_update(batch_msgs, ["onextel_message_id", "status", "submitted_at", "failure_reason"])
            dispatched_now += len(batch_msgs)
            pct = (dispatched_now / queued_count * 100) if queued_count else 100
            print(f"  -> Submitted to Carrier: {dispatched_now}/{queued_count} ({pct:.1f}%)...")

    # ---------------------------------------------------------
    # PART 2: Dispatch any remaining PENDING contacts
    # ---------------------------------------------------------
    if pending_contacts_count > 0:
        print(f"\n[PART 2] Generating & sending {pending_contacts_count} PENDING contacts...")
        user_wallet = getattr(user, "wallet", None)
        while True:
            pending_contacts = list(
                CampaignContact.objects.filter(
                    campaign=campaign,
                    status=CampaignContact.Status.PENDING,
                ).order_by("id")[:batch_size]
            )

            if not pending_contacts:
                break

            messages_to_create = []
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

            created_msgs = Message.objects.bulk_create(messages_to_create)

            rcs_items = []
            contacts_to_update = []
            transactions_to_create = []
            for idx, contact in enumerate(pending_contacts):
                msg = created_msgs[idx]
                contact.status = CampaignContact.Status.DISPATCHED
                contact.message = msg
                contacts_to_update.append(contact)
                rcs_items.append(build_onextel_rcs_item(msg))
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

            try:
                results = client.send_messages(rcs_items, prefix="+91")
                for msg in created_msgs:
                    msg.status = Message.Status.SUBMITTED
                    msg.submitted_at = timezone.now()
            except Exception as api_err:
                for msg in created_msgs:
                    msg.failure_reason = str(api_err)[:250]

            Message.objects.bulk_update(created_msgs, ["onextel_message_id", "status", "submitted_at", "failure_reason"])
            CampaignContact.objects.bulk_update(contacts_to_update, ["status", "message"])
            if transactions_to_create:
                WalletTransaction.objects.bulk_create(transactions_to_create)

    # Final wrap-up
    campaign.refresh_from_db()
    campaign.processed_count = CampaignContact.objects.filter(
        campaign=campaign,
        status__in=[CampaignContact.Status.DISPATCHED, CampaignContact.Status.DELIVERED, CampaignContact.Status.FAILED],
    ).count()
    campaign.status = Campaign.Status.COMPLETED
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=["processed_count", "status", "completed_at", "updated_at"])

    try:
        c_date = campaign.created_at.date() if campaign.created_at else timezone.now().date()
        sync_user_daily_mis(user_id=campaign.user_id, date=c_date)
        sync_live_status(user_id=campaign.user_id, date=c_date)
    except Exception:
        pass

    elapsed = time.time() - t_start
    print(f"\n==================================================")
    print(f"SUCCESS! Finished submitting {dispatched_now} messages to OneXtel in {elapsed:.1f} seconds!")
    print(f"Now carrier will deliver to handsets and DLRs will start arriving.")
    print(f"==================================================")


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "1acd4fe0-0954-4c07-885b-a8ef0bd58249"
    fast_dispatch(cid)
