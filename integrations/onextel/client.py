"""OneXtel RCS API Client.

Centralized integration module for communicating with the OneXtel vendor API.
Supports mock mode in development via ONEXTEL_MOCK setting.
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class OneXtelClientError(Exception):
    """Exception raised when an API error occurs with OneXtel."""
    pass


class OneXtelClient:
    """Client for OneXtel RCS API endpoints."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        mock: Optional[bool] = None,
    ):
        self.api_key = api_key or getattr(settings, "ONEXTEL_API_KEY", "")
        self.base_url = (base_url or getattr(settings, "ONEXTEL_BASE_URL", "https://api.onexaura.com")).rstrip("/")
        self.mock = mock if mock is not None else getattr(settings, "ONEXTEL_MOCK", False)

    def _get_headers(self, content_type: Optional[str] = "application/json") -> Dict[str, str]:
        headers = {"apikey": self.api_key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def create_template(
        self,
        data: Dict[str, Any],
        sender_profile_name: str,
        update_by: str,
        message_type: str,
        multimedia_files: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Create an RCS template with OneXtel (multipart/form-data)."""
        if self.mock:
            mock_id = f"mock-tpl-{uuid.uuid4().hex[:8]}"
            logger.info("OneXtel mock create_template called: %s", mock_id)
            return {
                "status": "success",
                "code": 200,
                "message": "Template submitted successfully (mock)",
                "templateId": mock_id,
                "data": data,
            }

        endpoint = f"{self.base_url}/rcs/templates"
        payload = {
            "data": json.dumps(data) if isinstance(data, dict) else data,
            "sender_profile_name": sender_profile_name,
            "update_by": update_by,
            "message_type": message_type,
        }
        # OneXtel requires multipart/form-data even when no media files are attached
        files = multimedia_files if multimedia_files else {"dummy": ("", "")}
        headers = self._get_headers(content_type=None)

        try:
            response = requests.post(endpoint, data=payload, files=files, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error("OneXtel create_template failed: %s", e)
            raise OneXtelClientError(f"Failed to create template: {e}") from e

    def fetch_templates(
        self,
        sender_profile: str,
        message_type: Optional[str] = None,
        status: Optional[str] = None,
        created_date: Optional[str] = None,
        template_name: Optional[str] = None,
        page_no: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Fetch templates from OneXtel."""
        if self.mock:
            logger.info("OneXtel mock fetch_templates called for %s", template_name or sender_profile)
            if template_name:
                mock_templates = [{
                    "name": template_name,
                    "templateName": template_name,
                    "senderProfile": sender_profile,
                    "status": "Approved",
                    "channel": "RCS",
                    "type": "text_message",
                    "textMessageContent": f"Hello [name], this is mock template {template_name}.",
                    "suggestions": [],
                }]
            else:
                norm_m = (message_type or "promotional").lower()
                mock_templates = [
                    {
                        "name": f"mock_welcome_{norm_m}",
                        "templateName": f"mock_welcome_{norm_m}",
                        "senderProfile": sender_profile,
                        "status": "Approved",
                        "channel": "RCS",
                        "type": "text_message",
                        "textMessageContent": "Welcome to [brand_name]! Your verification code is [code].",
                        "suggestions": [{"suggestionType": "reply", "displayText": "Verify", "postback": "verify_click"}],
                    },
                    {
                        "name": f"mock_rich_offer_{norm_m}",
                        "templateName": f"mock_rich_offer_{norm_m}",
                        "senderProfile": sender_profile,
                        "status": "Approved",
                        "channel": "RCS",
                        "type": "rich_card",
                        "standAlone": {
                            "cardTitle": "Exclusive [discount]% Off Deal!",
                            "cardDescription": "Use code [promo_code] on your order today.",
                            "mediaUrl": "https://placehold.co/600x400/2563eb/ffffff.png?text=Offer",
                            "suggestions": [{"suggestionType": "url_action", "displayText": "Claim Offer", "url": "https://example.com/deal", "postback": "claim"}],
                        },
                    },
                ]
            return {
                "status": "success",
                "code": 200,
                "totalCount": len(mock_templates),
                "data": mock_templates,
                "templates": mock_templates,
            }

        endpoint = f"{self.base_url}/templates/fetch"
        payload: Dict[str, Any] = {
            "channel": "RCS",
            "senderProfile": sender_profile,
            "pageNo": page_no,
            "limit": limit,
        }
        norm_mtype = str(message_type or "promotional").lower()
        if norm_mtype not in ["promotional", "transactional", "otp"]:
            norm_mtype = "promotional"
        payload["messageType"] = norm_mtype
        if status:
            payload["status"] = status
        if created_date:
            payload["createdDate"] = created_date
        if template_name:
            payload["templateName"] = template_name

        headers = self._get_headers(content_type="application/json")
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error("OneXtel fetch_templates failed: %s", e)
            raise OneXtelClientError(f"Failed to fetch templates: {e}") from e

    def send_messages(
        self,
        rcs_items: List[Dict[str, Any]],
        prefix: str = "+91",
        url_shortener: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Send RCS messages via OneXtel POST /send_sms endpoint.

        Response format: [{"messageId": "...", "code": 200, "status": "success"}]
        """
        if self.mock:
            results = []
            for item in rcs_items:
                mock_msg_id = f"onex-{uuid.uuid4().hex[:12]}"
                results.append({
                    "messageId": mock_msg_id,
                    "code": 200,
                    "status": "success",
                    "recipient": item.get("recipient"),
                })
            logger.info("OneXtel mock send_messages called for %d messages", len(rcs_items))
            return results

        endpoint = f"{self.base_url}/send_sms"
        payload: Dict[str, Any] = {
            "rcs": rcs_items,
            "prefix": prefix,
        }
        if url_shortener:
            payload["urlShortener"] = url_shortener
        headers = self._get_headers(content_type="application/json")
        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error("OneXtel send_messages failed: %s", e)
            raise OneXtelClientError(f"Failed to send RCS messages: {e}") from e

    def send_message(
        self,
        rcs_item: Dict[str, Any],
        prefix: str = "+91",
        url_shortener: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convenience method for sending a single RCS message."""
        results = self.send_messages([rcs_item], prefix=prefix, url_shortener=url_shortener)
        if not results:
            raise OneXtelClientError("OneXtel returned an empty response list for message.")
        return results[0]
