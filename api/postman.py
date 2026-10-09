"""Postman Collection v2.1 export generator for RCS Portal Public Client API."""
from typing import Any, Dict


def get_postman_collection(request=None) -> Dict[str, Any]:
    """Generate complete Postman collection v2.1 schema for API v1."""
    base_url = "{{baseUrl}}"
    if request:
        base_url = request.build_absolute_uri("/").rstrip("/")

    return {
        "info": {
            "name": "RCS Portal Client API v1",
            "description": "Public enterprise API for RCS messaging, templates, and delivery status.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "version": "1.0.0",
        },
        "variable": [
            {"key": "baseUrl", "value": base_url, "type": "string"},
            {"key": "apiKey", "value": "rcs_YOUR_API_KEY_HERE", "type": "string"},
        ],
        "auth": {
            "type": "apikey",
            "apikey": [
                {"key": "key", "value": "apikey", "type": "string"},
                {"key": "value", "value": "{{apiKey}}", "type": "string"},
                {"key": "in", "value": "header", "type": "string"},
            ],
        },
        "item": [
            {
                "name": "Messages",
                "item": [
                    {
                        "name": "Send Single Message",
                        "request": {
                            "method": "POST",
                            "header": [
                                {"key": "Content-Type", "value": "application/json"},
                                {"key": "Idempotency-Key", "value": "req_unique_id_12345"},
                            ],
                            "body": {
                                "mode": "raw",
                                "raw": '{\n  "recipient": "9876543210",\n  "sender_profile_id": 1,\n  "message_type": "PROMOTIONAL",\n  "content_type": "text",\n  "payload": {\n    "text": "Hello from RCS Portal API!"\n  }\n}',
                            },
                            "url": {"raw": "{{baseUrl}}/api/v1/messages/send/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "messages", "send", ""]},
                        },
                    },
                    {
                        "name": "Send Template with Custom Params",
                        "request": {
                            "method": "POST",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {
                                "mode": "raw",
                                "raw": '{\n  "recipient": "9876543210",\n  "sender_profile_id": 1,\n  "message_type": "TRANSACTIONAL",\n  "content_type": "template",\n  "template_id": 1,\n  "custom_params": {\n    "name": "Aman",\n    "otp": "459021"\n  }\n}',
                            },
                            "url": {"raw": "{{baseUrl}}/api/v1/messages/send/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "messages", "send", ""]},
                        },
                    },
                    {
                        "name": "Send Batch Recipients (up to 100)",
                        "request": {
                            "method": "POST",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {
                                "mode": "raw",
                                "raw": '{\n  "recipients": ["9876543210", "9876543211", "9876543212"],\n  "sender_profile_id": 1,\n  "message_type": "PROMOTIONAL",\n  "content_type": "text",\n  "payload": {"text": "Flash Sale notification!"}\n}',
                            },
                            "url": {"raw": "{{baseUrl}}/api/v1/messages/send/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "messages", "send", ""]},
                        },
                    },
                    {
                        "name": "Bulk Dispatch (up to 500)",
                        "request": {
                            "method": "POST",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {
                                "mode": "raw",
                                "raw": '{\n  "sender_profile_id": 1,\n  "message_type": "PROMOTIONAL",\n  "template_id": 1,\n  "messages": [\n    {\n      "recipient": "9876543210",\n      "custom_params": {"name": "Alice"}\n    },\n    {\n      "recipient": "9876543211",\n      "custom_params": {"name": "Bob"}\n    }\n  ]\n}',
                            },
                            "url": {"raw": "{{baseUrl}}/api/v1/messages/bulk/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "messages", "bulk", ""]},
                        },
                    },
                    {
                        "name": "Get Message Delivery Status",
                        "request": {
                            "method": "GET",
                            "url": {"raw": "{{baseUrl}}/api/v1/messages/YOUR_MESSAGE_UUID/status/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "messages", "YOUR_MESSAGE_UUID", "status", ""]},
                        },
                    },
                    {
                        "name": "List Messages (Filtered & Paginated)",
                        "request": {
                            "method": "GET",
                            "url": {
                                "raw": "{{baseUrl}}/api/v1/messages/?status=delivered&page=1",
                                "host": ["{{baseUrl}}"],
                                "path": ["api", "v1", "messages", ""],
                                "query": [
                                    {"key": "status", "value": "delivered"},
                                    {"key": "page", "value": "1"},
                                ],
                            },
                        },
                    },
                ],
            },
            {
                "name": "Templates",
                "item": [
                    {
                        "name": "List Approved Templates",
                        "request": {
                            "method": "GET",
                            "url": {"raw": "{{baseUrl}}/api/v1/templates/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "templates", ""]},
                        },
                    },
                    {
                        "name": "Get Template Detail",
                        "request": {
                            "method": "GET",
                            "url": {"raw": "{{baseUrl}}/api/v1/templates/1/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "templates", "1", ""]},
                        },
                    },
                    {
                        "name": "Submit Template for Approval",
                        "request": {
                            "method": "POST",
                            "header": [{"key": "Content-Type", "value": "application/json"}],
                            "body": {
                                "mode": "raw",
                                "raw": '{\n  "name": "order_alert_v1",\n  "template_type": "text_message",\n  "message_type": "TRANSACTIONAL",\n  "sender_profile_id": 1,\n  "payload": {\n    "textMessageContent": "Your order [order_id] is out for delivery."\n  }\n}',
                            },
                            "url": {"raw": "{{baseUrl}}/api/v1/templates/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "templates", ""]},
                        },
                    },
                ],
            },
            {
                "name": "Account & Balance",
                "item": [
                    {
                        "name": "Get Wallet Balance",
                        "request": {
                            "method": "GET",
                            "url": {"raw": "{{baseUrl}}/api/v1/balance/", "host": ["{{baseUrl}}"], "path": ["api", "v1", "balance", ""]},
                        },
                    },
                ],
            },
        ],
    }
