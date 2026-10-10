"""Service layer for syncing templates from OneXtel carrier API."""
import logging
from typing import Any, Dict, List, Optional
from django.utils import timezone
from integrations.onextel.client import OneXtelClient, OneXtelClientError
from wallet.models import SenderProfile
from .models import Template

logger = logging.getLogger(__name__)


def map_onextel_template_type(raw_type: str, item: Dict[str, Any]) -> str:
    """Map OneXtel template type/category to Template.TemplateType choice."""
    t_str = str(raw_type or item.get("category", "")).lower()
    if t_str == "carousel" or "carousellist" in [k.lower() for k in item.keys()]:
        return Template.TemplateType.CAROUSEL
    if t_str == "rich_card" or "standalone" in [k.lower() for k in item.keys()]:
        return Template.TemplateType.RICH_CARD
    if t_str == "text_message_with_media" or "documenturl" in [k.lower() for k in item.keys()]:
        return Template.TemplateType.TEXT_MESSAGE_WITH_MEDIA
    return Template.TemplateType.TEXT_MESSAGE


def map_onextel_status(raw_status: str) -> str:
    """Map raw status string to Template.Status choice."""
    raw = str(raw_status or "").upper()
    if "APPROV" in raw or raw == "ACTIVE":
        return Template.Status.APPROVED
    if "REJECT" in raw:
        return Template.Status.REJECTED
    return Template.Status.PENDING


def map_onextel_message_type(raw_mtype: str) -> str:
    """Map message type string to Template.MessageType choice."""
    m = str(raw_mtype or "").upper()
    if "TRANS" in m:
        return Template.MessageType.TRANSACTIONAL
    if "OTP" in m:
        return Template.MessageType.OTP
    return Template.MessageType.PROMOTIONAL


def sync_sender_profile_templates(
    user,
    sender_profile: SenderProfile,
    message_types: Optional[List[str]] = None,
    client: Optional[OneXtelClient] = None,
) -> Dict[str, Any]:
    """Fetch all carrier templates from OneXtel for a sender profile and import/update in database.

    Args:
        user: The owner User to assign newly imported templates to.
        sender_profile: The SenderProfile configured for the client.
        message_types: List of message types to query ('promotional', 'transactional', 'otp').
        client: Optional OneXtelClient instance (defaults to new instance).

    Returns:
        Dict with 'imported', 'updated', 'total', 'errors' count and messages.
    """
    if client is None:
        client = OneXtelClient()

    if not message_types:
        message_types = ["promotional", "transactional", "otp"]

    imported_count = 0
    updated_count = 0
    errors: List[str] = []

    profile_name = sender_profile.sender_profile_name or sender_profile.sender_id

    for m_type in message_types:
        page_no = 0
        limit = 50
        while True:
            try:
                resp = client.fetch_templates(
                    sender_profile=profile_name,
                    message_type=m_type,
                    page_no=page_no,
                    limit=limit,
                )
            except OneXtelClientError as e:
                err_msg = f"Failed to fetch {m_type} templates: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                break
            except Exception as e:
                err_msg = f"Unexpected error fetching {m_type} templates: {e}"
                logger.error(err_msg)
                errors.append(err_msg)
                break

            items = resp.get("data") or resp.get("templates") or []
            if not items:
                break

            for item in items:
                t_name = str(item.get("name") or item.get("templateName") or "").strip()
                if not t_name:
                    continue

                raw_status = str(item.get("status", "")).strip()
                status_choice = map_onextel_status(raw_status)
                template_type = map_onextel_template_type(item.get("type", ""), item)
                message_type_choice = map_onextel_message_type(item.get("messageType", m_type))

                # Check if template already exists
                existing = Template.objects.filter(name=t_name).first()
                if existing:
                    existing.status = status_choice
                    existing.onextel_status_raw = raw_status
                    existing.payload = item
                    existing.template_type = template_type
                    existing.message_type = message_type_choice
                    existing.last_synced_at = timezone.now()
                    # Ensure sender profile is attached if missing
                    if not existing.sender_profile_id:
                        existing.sender_profile = sender_profile
                    existing.save(update_fields=[
                        "status",
                        "onextel_status_raw",
                        "payload",
                        "template_type",
                        "message_type",
                        "sender_profile",
                        "last_synced_at",
                        "updated_at",
                    ])
                    updated_count += 1
                else:
                    Template.objects.create(
                        owner=user,
                        name=t_name,
                        template_type=template_type,
                        message_type=message_type_choice,
                        sender_profile=sender_profile,
                        payload=item,
                        status=status_choice,
                        onextel_status_raw=raw_status,
                        last_synced_at=timezone.now(),
                    )
                    imported_count += 1

            # Check if there are more pages
            if len(items) < limit:
                break
            page_no += 1

    return {
        "imported": imported_count,
        "updated": updated_count,
        "total": imported_count + updated_count,
        "errors": errors,
        "sender_profile": profile_name,
    }
