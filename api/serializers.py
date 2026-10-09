"""DRF Serializers for messages, templates, balance, and validation."""
from decimal import Decimal
from rest_framework import serializers

from messaging.models import Message
from templates_mgmt.models import Template
from wallet.models import SenderProfile


class SingleMessageResultSerializer(serializers.ModelSerializer):
    """Serialize Message object returning our UUID as the primary identifier."""

    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    content_type_display = serializers.CharField(source="get_content_type_display", read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "recipient",
            "prefix",
            "message_type",
            "content_type",
            "content_type_display",
            "status",
            "status_display",
            "cost",
            "failure_reason",
            "custref1",
            "custref2",
            "custref3",
            "custref4",
            "custref5",
            "custref6",
            "created_at",
            "submitted_at",
            "delivered_at",
            "read_at",
        ]


class MessageSendSerializer(serializers.Serializer):
    """Serializer for single or up to N recipients message dispatch."""

    recipient = serializers.CharField(max_length=20, required=False, allow_blank=True)
    recipients = serializers.ListField(
        child=serializers.CharField(max_length=20),
        required=False,
        allow_empty=False,
        max_length=100,
        help_text="Optional array of up to 100 recipients for batch dispatch",
    )
    sender_profile_id = serializers.IntegerField(required=True)
    message_type = serializers.ChoiceField(
        choices=Message.MessageType.choices,
        default=Message.MessageType.PROMOTIONAL,
    )
    content_type = serializers.ChoiceField(
        choices=Message.ContentType.choices,
        default=Message.ContentType.TEXT,
    )
    payload = serializers.DictField(required=False, default=dict)
    template_id = serializers.IntegerField(required=False, allow_null=True)
    custom_params = serializers.DictField(required=False, default=dict)
    ttl = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    custref1 = serializers.CharField(max_length=100, required=False, default="")
    custref2 = serializers.CharField(max_length=100, required=False, default="")
    custref3 = serializers.CharField(max_length=100, required=False, default="")
    custref4 = serializers.CharField(max_length=100, required=False, default="")
    custref5 = serializers.CharField(max_length=100, required=False, default="")
    custref6 = serializers.CharField(max_length=100, required=False, default="")

    def validate(self, attrs):
        recipient = attrs.get("recipient")
        recipients = attrs.get("recipients")

        if not recipient and not recipients:
            raise serializers.ValidationError("Either 'recipient' or 'recipients' must be specified.")

        return attrs


class BulkMessageItemSerializer(serializers.Serializer):
    """Individual item in bulk message payload."""

    recipient = serializers.CharField(max_length=20)
    custom_params = serializers.DictField(required=False, default=dict)
    custref1 = serializers.CharField(max_length=100, required=False, default="")


class BulkMessageSendSerializer(serializers.Serializer):
    """Batch message dispatch serializer."""

    sender_profile_id = serializers.IntegerField(required=True)
    message_type = serializers.ChoiceField(
        choices=Message.MessageType.choices,
        default=Message.MessageType.PROMOTIONAL,
    )
    template_id = serializers.IntegerField(required=False, allow_null=True)
    content_type = serializers.ChoiceField(
        choices=Message.ContentType.choices,
        default=Message.ContentType.TEMPLATE,
    )
    payload = serializers.DictField(required=False, default=dict)
    messages = serializers.ListField(
        child=BulkMessageItemSerializer(),
        min_length=1,
        max_length=500,
        help_text="Array of up to 500 individual recipient messages",
    )


class TemplateListSerializer(serializers.ModelSerializer):
    """Serialize pre-approved templates for client inspection."""

    detected_placeholders = serializers.ListField(read_only=True)
    sender_profile_name = serializers.CharField(source="sender_profile.sender_profile_name", read_only=True)

    class Meta:
        model = Template
        fields = [
            "id",
            "name",
            "template_type",
            "message_type",
            "sender_profile_name",
            "status",
            "payload",
            "detected_placeholders",
            "created_at",
            "updated_at",
        ]


class TemplateCreateSerializer(serializers.ModelSerializer):
    """Create and submit a template via API."""

    sender_profile_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = Template
        fields = [
            "id",
            "name",
            "template_type",
            "message_type",
            "sender_profile_id",
            "payload",
            "status",
            "created_at",
        ]
        read_only_fields = ["id", "status", "created_at"]

    def validate_sender_profile_id(self, value):
        user = self.context["request"].user
        if not SenderProfile.objects.filter(id=value, user=user, is_active=True).exists():
            raise serializers.ValidationError("Invalid or inactive sender profile.")
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        sp_id = validated_data.pop("sender_profile_id")
        sp = SenderProfile.objects.get(id=sp_id)
        return Template.objects.create(
            owner=user,
            sender_profile=sp,
            status=Template.Status.DRAFT,
            **validated_data,
        )


class BalanceSerializer(serializers.Serializer):
    """Client prepaid wallet balance."""

    balance = serializers.DecimalField(max_digits=14, decimal_places=4)
    currency = serializers.CharField(max_length=10)
    formatted = serializers.CharField(max_length=50)
