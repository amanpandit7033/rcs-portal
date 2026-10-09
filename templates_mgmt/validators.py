"""Comprehensive server-side payload validation for RCS templates and suggestions."""
from typing import Any, Dict, List
from django.core.exceptions import ValidationError


VALID_SUGGESTION_TYPES = {
    "reply",
    "dialer_action",
    "url_action",
    "view_location_latlong",
    "view_location_query",
    "share_location",
    "calendar_event",
}


def validate_suggestion(suggestion: Dict[str, Any], index: int = 0) -> None:
    """Validate an individual suggestion (action button)."""
    if not isinstance(suggestion, dict):
        raise ValidationError(f"Suggestion #{index + 1} must be an object.")

    display_text = str(suggestion.get("displayText", "")).strip()
    postback = str(suggestion.get("postback", "")).strip()
    s_type = suggestion.get("suggestionType")

    if not display_text:
        raise ValidationError(f"Suggestion #{index + 1}: 'displayText' is required.")
    if not postback:
        raise ValidationError(f"Suggestion #{index + 1}: 'postback' is required.")
    if s_type not in VALID_SUGGESTION_TYPES:
        raise ValidationError(
            f"Suggestion #{index + 1}: Invalid 'suggestionType' '{s_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_SUGGESTION_TYPES))}."
        )

    # Validate specific suggestion types
    if s_type == "dialer_action":
        phone = str(suggestion.get("phoneNumber", "")).strip()
        if not phone:
            raise ValidationError(f"Suggestion #{index + 1} ('dialer_action') requires 'phoneNumber'.")

    elif s_type == "url_action":
        url = str(suggestion.get("url", "")).strip()
        if not url:
            raise ValidationError(f"Suggestion #{index + 1} ('url_action') requires 'url'.")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValidationError(f"Suggestion #{index + 1} ('url_action'): 'url' must start with http:// or https://.")

    elif s_type == "view_location_latlong":
        label = str(suggestion.get("label", "")).strip()
        lat = suggestion.get("latitude")
        lng = suggestion.get("longitude")

        if not label:
            raise ValidationError(f"Suggestion #{index + 1} ('view_location_latlong') requires 'label'.")
        if lat is None or lat == "":
            raise ValidationError(f"Suggestion #{index + 1} ('view_location_latlong') requires 'latitude'.")
        if lng is None or lng == "":
            raise ValidationError(f"Suggestion #{index + 1} ('view_location_latlong') requires 'longitude'.")
        try:
            float(lat)
            float(lng)
        except (ValueError, TypeError):
            raise ValidationError(f"Suggestion #{index + 1} ('view_location_latlong'): 'latitude' and 'longitude' must be numeric.")

    elif s_type == "view_location_query":
        query = str(suggestion.get("query", "")).strip()
        if not query:
            raise ValidationError(f"Suggestion #{index + 1} ('view_location_query') requires 'query'.")

    elif s_type == "calendar_event":
        for field in ["title", "description", "startTime", "endTime", "timeZone"]:
            val = str(suggestion.get(field, "")).strip()
            if not val:
                raise ValidationError(f"Suggestion #{index + 1} ('calendar_event') requires '{field}'.")


def validate_suggestions_list(suggestions: List[Dict[str, Any]]) -> None:
    """Validate a list of suggestions."""
    if not isinstance(suggestions, list):
        raise ValidationError("'suggestions' must be a list.")
    for idx, s in enumerate(suggestions):
        validate_suggestion(s, index=idx)


def validate_template_payload(template_type: str, payload: Dict[str, Any]) -> None:
    """Validate the structured payload conforming to template_type rules."""
    if not isinstance(payload, dict):
        raise ValidationError("Payload must be a valid JSON dictionary.")

    # 1. Text Message
    if template_type == "text_message":
        text = str(payload.get("textMessageContent", "")).strip()
        if not text:
            raise ValidationError("text_message requires non-empty 'textMessageContent'.")
        if "suggestions" in payload and payload["suggestions"]:
            validate_suggestions_list(payload["suggestions"])

    # 2. Text Message with Media
    elif template_type == "text_message_with_media":
        doc_url = str(payload.get("documentUrl", "")).strip()
        if not doc_url:
            raise ValidationError("text_message_with_media requires 'documentUrl'.")

        msg_order = payload.get("messageOrder")
        if msg_order not in ["media_at_top", "text_message_at_top"]:
            raise ValidationError("messageOrder must be either 'media_at_top' or 'text_message_at_top'.")

        text = str(payload.get("textMessageContent", "")).strip()
        if not text:
            raise ValidationError("text_message_with_media requires 'textMessageContent'.")

        if "suggestions" in payload and payload["suggestions"]:
            validate_suggestions_list(payload["suggestions"])

    # 3. Rich Card (Standalone)
    elif template_type == "rich_card":
        height = payload.get("height")
        if height not in ["SHORT_HEIGHT", "MEDIUM_HEIGHT", "TALL_HEIGHT"]:
            raise ValidationError("rich_card 'height' must be SHORT_HEIGHT, MEDIUM_HEIGHT, or TALL_HEIGHT.")

        orientation = payload.get("orientation")
        if orientation not in ["VERTICAL", "HORIZONTAL"]:
            raise ValidationError("rich_card 'orientation' must be VERTICAL or HORIZONTAL.")

        standalone = payload.get("standAlone")
        if not isinstance(standalone, dict):
            raise ValidationError("rich_card requires a 'standAlone' object.")

        title = str(standalone.get("cardTitle", "")).strip()
        desc = str(standalone.get("cardDescription", "")).strip()
        if not title and not desc:
            raise ValidationError("rich_card 'standAlone' requires at least a 'cardTitle' or 'cardDescription'.")

        if "suggestions" in standalone and standalone["suggestions"]:
            validate_suggestions_list(standalone["suggestions"])

    # 4. Carousel
    elif template_type == "carousel":
        width = payload.get("width")
        if width not in ["SMALL_WIDTH", "MEDIUM_WIDTH"]:
            raise ValidationError("carousel 'width' must be SMALL_WIDTH or MEDIUM_WIDTH.")

        height = payload.get("height")
        if height not in ["SHORT_HEIGHT", "TALL_HEIGHT"]:
            raise ValidationError("carousel 'height' must be SHORT_HEIGHT or TALL_HEIGHT.")

        carousel_list = payload.get("carouselList")
        if not isinstance(carousel_list, list):
            raise ValidationError("carousel requires a 'carouselList' array.")

        if len(carousel_list) < 2 or len(carousel_list) > 10:
            raise ValidationError(
                f"carousel must contain between 2 and 10 cards. Current cards count: {len(carousel_list)}."
            )

        state = payload.get("templateState", "Submit")
        if state not in ["Create", "Submit"]:
            raise ValidationError("carousel 'templateState' must be 'Create' or 'Submit'.")

        for idx, card in enumerate(carousel_list):
            if not isinstance(card, dict):
                raise ValidationError(f"Carousel card #{idx + 1} must be an object.")
            title = str(card.get("cardTitle", "")).strip()
            desc = str(card.get("cardDescription", "")).strip()
            if not title and not desc:
                raise ValidationError(f"Carousel card #{idx + 1} requires at least a cardTitle or cardDescription.")
            if "suggestions" in card and card["suggestions"]:
                validate_suggestions_list(card["suggestions"])

    else:
        raise ValidationError(f"Unknown template_type: '{template_type}'.")
