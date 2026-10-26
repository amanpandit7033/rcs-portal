import logging
import os
import sys
import threading
import time
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CampaignsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "campaigns"

    def ready(self):
        # Do not start ticker thread during management commands
        skip_cmds = {"migrate", "makemigrations", "collectstatic", "check", "test", "inspectdb"}
        if any(cmd in sys.argv for cmd in skip_cmds):
            return

        # In runserver, start only in the active child reloader process to prevent duplicate threads
        if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        def _ticker():
            while True:
                try:
                    time.sleep(10)
                    from .tasks import check_scheduled_campaigns
                    check_scheduled_campaigns()
                except Exception as e:
                    logger.debug("Scheduled campaign ticker error: %s", e)

        ticker_thread = threading.Thread(target=_ticker, name="ScheduledCampaignTicker", daemon=True)
        ticker_thread.start()
        logger.info("Scheduled campaign background ticker thread started.")
