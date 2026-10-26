"""Management command to populate historical LiveStatus records from Message logs."""
from datetime import datetime
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from reports.models import LiveStatus, sync_live_status

User = get_user_model()


class Command(BaseCommand):
    help = "Populates LiveStatus records from existing old/historical Message logs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Specific date to sync (YYYY-MM-DD). If omitted, syncs all historical dates.",
        )
        parser.add_argument(
            "--from-date",
            type=str,
            help="Start date of range to sync (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            help="End date of range to sync (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--username",
            type=str,
            help="Sync only for a specific username. If omitted, syncs for all users.",
        )

    def handle(self, *args, **options):
        date_str = options.get("date")
        from_date_str = options.get("from_date")
        to_date_str = options.get("to_date")
        username = options.get("username")

        def parse_d(val):
            if not val:
                return None
            try:
                return datetime.strptime(val, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format: {val}. Use YYYY-MM-DD."))
                return None

        sync_date = parse_d(date_str) if date_str else None
        from_d = parse_d(from_date_str) if from_date_str else None
        to_d = parse_d(to_date_str) if to_date_str else None

        user_id = None
        if username:
            user = User.objects.filter(username__iexact=username).first()
            if not user:
                self.stderr.write(self.style.ERROR(f"User '{username}' not found."))
                return
            user_id = user.id

        self.stdout.write(self.style.NOTICE("Starting LiveStatus historical sync from Message logs..."))
        sync_live_status(user_id=user_id, date=sync_date, from_date=from_d, to_date=to_d)

        total_records = LiveStatus.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully synced LiveStatus! Total records in table: {total_records}"
            )
        )
