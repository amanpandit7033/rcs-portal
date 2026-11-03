"""Management command to reconcile carrier message IDs and statuses from OneXtel dashboard CSV export."""
import csv
import os
from typing import Dict, Optional
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from campaigns.models import Campaign, CampaignContact
from messaging.models import Message


STATUS_RANK = {
    Message.Status.FAILED: 0,
    Message.Status.QUEUED: 10,
    Message.Status.SUBMITTED: 20,
    Message.Status.SENT: 30,
    Message.Status.DELIVERED: 40,
    Message.Status.READ: 50,
}

STATUS_MAP = {
    "submitted": Message.Status.SUBMITTED,
    "sent": Message.Status.SENT,
    "delivered": Message.Status.DELIVERED,
    "read": Message.Status.READ,
    "failed": Message.Status.FAILED,
    "undelivered": Message.Status.FAILED,
    "rejected": Message.Status.FAILED,
}


def find_column(headers: list, candidates: list) -> Optional[str]:
    header_lower_map = {h.strip().lower().replace(" ", "_"): h for h in headers}
    for c in candidates:
        if c in header_lower_map:
            return header_lower_map[c]
    return None


class Command(BaseCommand):
    help = (
        "Reconcile stored message IDs and statuses with OneXtel carrier dashboard CSV export.\n"
        "Compares recipient phone numbers, checks for mismatched onextel_message_id, "
        "and optionally corrects them with --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to OneXtel export CSV file.")
        parser.add_argument("campaign_id", type=str, help="Campaign UUID or ID.")
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Apply corrections to the database. By default, runs in dry-run mode.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Dry run without modifying the database (default).",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_file"]
        campaign_id = options["campaign_id"]
        apply_changes = options["apply"]

        if not os.path.exists(csv_path):
            raise CommandError(f"CSV file not found: {csv_path}")

        try:
            campaign = Campaign.objects.get(id=campaign_id)
        except (Campaign.DoesNotExist, ValueError):
            raise CommandError(f"Campaign with id '{campaign_id}' does not exist.")

        self.stdout.write(
            self.style.NOTICE(
                f"Starting reconciliation for Campaign '{campaign.name}' (ID: {campaign.id})\n"
                f"Mode: {'APPLY (live updates)' if apply_changes else 'DRY RUN (no database changes)'}"
            )
        )

        # Load portal messages for this campaign
        portal_messages = list(
            Message.objects.filter(campaign_id=campaign.id).select_related("sender_profile")
        )
        msg_by_recipient: Dict[str, Message] = {}
        for msg in portal_messages:
            digits = "".join(filter(str.isdigit, str(msg.recipient or "")))
            last10 = digits[-10:] if len(digits) >= 10 else digits
            if last10:
                msg_by_recipient[last10] = msg

        # Read CSV file
        seen_recipients = set()
        matched_count = 0
        mismatched_count = 0
        missing_in_portal_count = 0

        with open(csv_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            mid_col = find_column(headers, ["message_id", "messageid", "msg_id", "msgid", "carrier_message_id", "id"])
            recip_col = find_column(headers, ["recipient", "mobile", "phone", "number", "contact", "destination"])
            status_col = find_column(headers, ["status", "delivery_status", "dlr_status", "state"])

            if not mid_col or not recip_col:
                raise CommandError(
                    f"CSV headers missing required columns. Found headers: {headers}. "
                    f"Need message_id and recipient/mobile."
                )

            for row in reader:
                op_mid = str(row.get(mid_col) or "").strip()
                raw_recip = str(row.get(recip_col) or "").strip()
                raw_status = str(row.get(status_col) or "").strip().lower() if status_col else ""

                clean_digits = "".join(filter(str.isdigit, raw_recip))
                last10 = clean_digits[-10:] if len(clean_digits) >= 10 else clean_digits
                if not last10:
                    continue

                seen_recipients.add(last10)
                msg = msg_by_recipient.get(last10)

                if not msg:
                    missing_in_portal_count += 1
                    continue

                op_status = STATUS_MAP.get(raw_status)

                # Check carrier ID match
                if msg.onextel_message_id == op_mid and op_mid:
                    matched_count += 1
                else:
                    mismatched_count += 1
                    self.stdout.write(
                        f"  [MISMATCH] Recipient: {last10} | Stored ID: '{msg.onextel_message_id}' -> Operator ID: '{op_mid}'"
                        f" | Status: portal='{msg.status}', operator='{raw_status}'"
                    )

                    if apply_changes:
                        fields_to_save = ["onextel_message_id"]
                        msg.onextel_message_id = op_mid

                        # Advance status if operator status is higher rank
                        if op_status:
                            cur_rank = STATUS_RANK.get(msg.status, 0)
                            new_rank = STATUS_RANK.get(op_status, 0)
                            if new_rank > cur_rank:
                                msg.status = op_status
                                fields_to_save.append("status")
                                if op_status == Message.Status.DELIVERED and not msg.delivered_at:
                                    msg.delivered_at = timezone.now()
                                    fields_to_save.append("delivered_at")

                        msg.save(update_fields=fields_to_save)

                        # Update campaign contact if status was updated
                        if "status" in fields_to_save:
                            contact_status_map = {
                                Message.Status.DELIVERED: CampaignContact.Status.DELIVERED,
                                Message.Status.READ: CampaignContact.Status.READ,
                                Message.Status.FAILED: "failed",
                            }
                            c_status = contact_status_map.get(msg.status)
                            if c_status:
                                CampaignContact.objects.filter(
                                    campaign_id=campaign.id, message_id=msg.id
                                ).update(status=c_status)

        missing_at_operator_count = sum(
            1 for recip in msg_by_recipient.keys() if recip not in seen_recipients
        )

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("Reconciliation Report Summary:"))
        self.stdout.write(f"  Matched message IDs      : {matched_count}")
        self.stdout.write(f"  Mismatched message IDs   : {mismatched_count}")
        self.stdout.write(f"  Missing in portal        : {missing_in_portal_count}")
        self.stdout.write(f"  Missing at operator      : {missing_at_operator_count}")
        self.stdout.write(f"  Total Portal Messages    : {len(portal_messages)}")
        self.stdout.write(f"  Total Operator Numbers   : {len(seen_recipients)}")
        self.stdout.write("=" * 50)

        if not apply_changes and mismatched_count > 0:
            self.stdout.write(
                self.style.WARNING("To apply corrections, rerun with --apply")
            )
