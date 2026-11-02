"""Reprocess all pending/unmatched DLRLog records using carrier message ID and recipient phone fallbacks."""
import os
import sys
import django
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from messaging.models import DLRLog, Message
from messaging.tasks import process_dlr_task
from reports.models import sync_user_daily_mis, sync_live_status
from campaigns.models import Campaign


def reprocess_all_dlrs():
    unprocessed_ids = list(DLRLog.objects.filter(processed=False).values_list("id", flat=True).order_by("id"))
    total = len(unprocessed_ids)
    print(f"==================================================")
    print(f"Found {total} unprocessed DLRs in database.")
    print(f"Reprocessing and matching to messages now...")
    print(f"==================================================")

    success = 0
    errors = 0
    t0 = time.time()

    for idx, log_id in enumerate(unprocessed_ids, 1):
        try:
            process_dlr_task(log_id)
            success += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"Error on DLR #{log_id}: {e}")

        if idx % 200 == 0 or idx == total:
            pct = (idx / total * 100) if total else 100
            print(f"Processed {idx}/{total} ({pct:.1f}%) DLRs (Matched: {success}, Errors: {errors})...")

    # Sync live status and campaign stats for today
    print("\nSyncing MIS and Live Status reports...")
    try:
        sync_live_status()
        sync_user_daily_mis()
    except Exception as sync_e:
        print(f"Sync note: {sync_e}")

    elapsed = time.time() - t0
    print(f"==================================================")
    print(f"COMPLETED! Processed {success} DLRs in {elapsed:.1f} seconds.")
    print(f"==================================================")


if __name__ == "__main__":
    reprocess_all_dlrs()
