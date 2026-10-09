"""Management command to simulate carrier DLR webhook callbacks for local development."""
import json
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from messaging.models import DLRLog, Message
from messaging.tasks import process_dlr_task


class Command(BaseCommand):
    help = "Simulate an incoming OneXtel RCS delivery receipt (DLR) webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--message-id",
            type=str,
            help="OneXtel Message ID or Internal Message UUID. If omitted, uses the most recent message.",
        )
        parser.add_argument(
            "--status",
            type=str,
            default="delivered",
            choices=["delivered", "read", "failed", "sent"],
            help="Delivery status to simulate (default: delivered).",
        )
        parser.add_argument(
            "--reason",
            type=str,
            default="",
            help="Failure reason description if status is 'failed'.",
        )

    def handle(self, *args, **options):
        msg_id_arg = options.get("message_id")
        status_arg = options.get("status")
        reason_arg = options.get("reason") or "Simulated carrier delivery failure"

        if msg_id_arg:
            msg = Message.objects.filter(onextel_message_id=msg_id_arg).first()
            if not msg:
                msg = Message.objects.filter(id=msg_id_arg).first()
            if not msg:
                raise CommandError(f"No message found matching identifier '{msg_id_arg}'.")
        else:
            msg = Message.objects.order_by("-created_at").first()
            if not msg:
                raise CommandError("No messages exist in the database to simulate DLR for.")

        carrier_id = msg.onextel_message_id or f"mock-carrier-{msg.id.hex[:10]}"
        if not msg.onextel_message_id:
            msg.onextel_message_id = carrier_id
            msg.save(update_fields=["onextel_message_id"])

        simulated_payload = {
            "messageId": carrier_id,
            "status": status_arg.upper(),
            "timestamp": timezone.now().isoformat(),
            "recipient": msg.recipient,
            "channel": "RCS",
        }
        if status_arg == "failed":
            simulated_payload["reason"] = reason_arg
            simulated_payload["errorCode"] = "DND_FILTER_REJECT"

        self.stdout.write(self.style.NOTICE(f"Simulating DLR for Message {msg.id} (Carrier ID: {carrier_id}) with status: {status_arg.upper()}"))

        # Create DLRLog
        dlr_log = DLRLog.objects.create(
            payload=simulated_payload,
            source_ip="127.0.0.1",
            processed=False,
        )

        # Execute task
        process_dlr_task(dlr_log.id)

        msg.refresh_from_db()
        dlr_log.refresh_from_db()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully processed DLRLog #{dlr_log.id}! Message status updated to: {msg.status.upper()} (Refunded: {msg.is_refunded})"
            )
        )
