"""Celery tasks for synchronizing pending RCS templates with OneXtel API."""
import logging
from celery import shared_task
from django.utils import timezone
from integrations.onextel.client import OneXtelClient
from .models import Template

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def sync_pending_templates(self):
    """Periodic Beat task to poll OneXtel Fetch Template API for pending templates."""
    pending_templates = Template.objects.filter(status=Template.Status.PENDING).select_related("sender_profile")
    total_count = pending_templates.count()

    if total_count == 0:
        return "No pending templates to sync."

    logger.info("Starting sync for %d pending RCS templates...", total_count)
    client = OneXtelClient()
    updated_count = 0

    for template in pending_templates:
        try:
            profile_name = template.sender_profile.sender_profile_name
            resp = client.fetch_templates(
                sender_profile=profile_name,
                template_name=template.name,
                message_type=template.message_type,
            )

            # Check matching template from response (OneXtel returns 'data' or 'templates')
            templates_list = resp.get("data") or resp.get("templates") or []
            matched = None
            for t in templates_list:
                t_name = t.get("name") or t.get("templateName")
                if t_name == template.name:
                    matched = t
                    break

            if matched:
                raw_status = str(matched.get("status", "")).upper()
                template.onextel_status_raw = raw_status
                template.last_synced_at = timezone.now()

                if "APPROV" in raw_status or raw_status == "ACTIVE":
                    template.status = Template.Status.APPROVED
                elif "REJECT" in raw_status:
                    template.status = Template.Status.REJECTED

                template.save(update_fields=["status", "onextel_status_raw", "last_synced_at", "updated_at"])
                updated_count += 1
            else:
                template.last_synced_at = timezone.now()
                template.save(update_fields=["last_synced_at"])

        except Exception as e:
            logger.error("Failed to sync template '%s': %s", template.name, e)

    return f"Synced {updated_count}/{total_count} pending templates."
