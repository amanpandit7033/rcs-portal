"""Tests for OneXtel RCS message ID mapping, batch dispatching, DLR reconciliation, and regression fixes."""
from decimal import Decimal
from unittest.mock import MagicMock, patch
import uuid
import pytest
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from campaigns.models import Campaign, CampaignContact
from messaging.models import DLRLog, Message
from messaging.tasks import (
    extract_onextel_id,
    is_entry_success,
    map_batch_response_to_messages,
    process_dlr_task,
    send_batch_messages_task,
)
from wallet.models import SenderProfile, Wallet


class MessageIdAndDlrReconciliationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="password123",
            role=User.Role.USER,
        )
        self.wallet = getattr(self.user, "wallet", None) or Wallet.objects.filter(user=self.user).first()
        if self.wallet:
            self.wallet.balance = Decimal("100.0000")
            self.wallet.save(update_fields=["balance"])
        else:
            self.wallet = Wallet.objects.create(user=self.user, balance=Decimal("100.0000"))
        self.sender_profile = SenderProfile.objects.create(
            user=self.user,
            sender_id="TestSender",
            sender_profile_name="TestSender",
            api_key="test-api-key",
        )

    def _create_message(self, recipient: str, status=Message.Status.QUEUED, onextel_id="", custref1=""):
        return Message.objects.create(
            user=self.user,
            sender_profile=self.sender_profile,
            recipient=recipient,
            message_type=Message.MessageType.PROMOTIONAL,
            content_type=Message.ContentType.TEXT,
            payload={"text": "Test message"},
            cost=Decimal("0.2500"),
            status=status,
            onextel_message_id=onextel_id,
            custref1=custref1,
            submitted_at=timezone.now() if status != Message.Status.QUEUED else None,
        )

    def test_batch_response_same_order_all_ids_correct_mapping(self):
        """1. Batch response in the same order with all IDs -> correct mapping."""
        m1 = self._create_message("919876543210")
        m2 = self._create_message("919876543211")
        m3 = self._create_message("919876543212")

        raw_response = [
            {"messageId": "carrier-id-1", "code": 200, "status": "success"},
            {"messageId": "carrier-id-2", "code": 200, "status": "success"},
            {"messageId": "carrier-id-3", "code": 200, "status": "success"},
        ]

        assignments, unassigned = map_batch_response_to_messages(raw_response, [m1, m2, m3])

        self.assertEqual(len(unassigned), 0)
        self.assertEqual(len(assignments), 3)
        self.assertEqual(assignments[0][0].id, m1.id)
        self.assertEqual(extract_onextel_id(assignments[0][1]), "carrier-id-1")
        self.assertEqual(assignments[1][0].id, m2.id)
        self.assertEqual(extract_onextel_id(assignments[1][1]), "carrier-id-2")
        self.assertEqual(assignments[2][0].id, m3.id)
        self.assertEqual(extract_onextel_id(assignments[2][1]), "carrier-id-3")

    def test_batch_response_fewer_entries_no_positional_assignment_stay_queued(self):
        """2. Response with fewer entries than messages -> no positional assignment, messages stay QUEUED without resending by default."""
        m1 = self._create_message("919876543210")
        m2 = self._create_message("919876543211")
        m3 = self._create_message("919876543212")

        # Response only returns 2 entries for 3 messages with no custref/phone
        raw_response = [
            {"messageId": "carrier-id-1", "code": 200, "status": "success"},
            {"messageId": "carrier-id-2", "code": 200, "status": "success"},
        ]

        assignments, unassigned = map_batch_response_to_messages(raw_response, [m1, m2, m3])

        # Positional fallback MUST be rejected
        self.assertEqual(len(assignments), 0)
        self.assertEqual(len(unassigned), 3)
        self.assertEqual(set(m.id for m in unassigned), {m1.id, m2.id, m3.id})

        # Test within send_batch_messages_task with mocked client:
        # Default ONEXTEL_RESEND_UNASSIGNED=False: do NOT call send_message_task
        with patch("messaging.tasks.OneXtelClient") as mock_client_cls, \
             patch("messaging.tasks.send_message_task.apply_async") as mock_apply_async:
            mock_client = MagicMock()
            mock_client.send_messages.return_value = raw_response
            mock_client_cls.return_value = mock_client

            send_batch_messages_task([str(m1.id), str(m2.id), str(m3.id)])

            # Reload messages from DB: they must stay QUEUED with empty onextel_message_id
            m1.refresh_from_db()
            m2.refresh_from_db()
            m3.refresh_from_db()

            self.assertEqual(m1.status, Message.Status.QUEUED)
            self.assertEqual(m1.onextel_message_id, "")
            self.assertEqual(m2.status, Message.Status.QUEUED)
            self.assertEqual(m2.onextel_message_id, "")
            self.assertEqual(m3.status, Message.Status.QUEUED)
            self.assertEqual(m3.onextel_message_id, "")

            # Single-send task fallback should NOT be triggered by default (avoiding duplicate RCS)
            self.assertEqual(mock_apply_async.call_count, 0)

        # When ONEXTEL_RESEND_UNASSIGNED is enabled, resend is called
        with patch("messaging.tasks.OneXtelClient") as mock_client_cls, \
             patch("messaging.tasks.send_message_task.apply_async") as mock_apply_async, \
             self.settings(ONEXTEL_RESEND_UNASSIGNED=True):
            mock_client = MagicMock()
            mock_client.send_messages.return_value = raw_response
            mock_client_cls.return_value = mock_client

            send_batch_messages_task([str(m1.id), str(m2.id), str(m3.id)])
            self.assertEqual(mock_apply_async.call_count, 3)

    def test_is_entry_success_tolerant(self):
        """Tolerant entry success checking."""
        # Status variations
        for st in ["success", "submitted", "queued", "accepted", "sent", "SUCCESS", "Queued"]:
            self.assertTrue(is_entry_success({"status": st}))

        # Code variations
        for cd in [200, 201, 202, "200", "201", "202"]:
            self.assertTrue(is_entry_success({"code": cd}))

        # MessageId present without explicit error code
        self.assertTrue(is_entry_success({"messageId": "msg-xyz"}))
        self.assertTrue(is_entry_success({"messageId": "msg-xyz", "code": 100}))

        # Explicit errors
        self.assertFalse(is_entry_success({"messageId": "msg-xyz", "code": 400}))
        self.assertFalse(is_entry_success({"code": 500}))
        self.assertFalse(is_entry_success({"status": "failed"}))
        self.assertFalse(is_entry_success({"status": "rejected"}))

    def test_send_message_task_does_not_raise_if_message_id_present(self):
        """In send_message_task, do not raise/retry for an entry that has a messageId."""
        msg = self._create_message("919876543210")
        with patch("messaging.tasks.OneXtelClient") as mock_client_cls:
            mock_client = MagicMock()
            # Carrier returns messageId with a non-200 code (e.g. 101 or unknown)
            mock_client.send_message.return_value = {"messageId": "carrier-id-custom", "code": 101}
            mock_client_cls.return_value = mock_client

            from messaging.tasks import send_message_task
            res = send_message_task(str(msg.id))
            self.assertEqual(res, "carrier-id-custom")

            msg.refresh_from_db()
            self.assertEqual(msg.status, Message.Status.SUBMITTED)
            self.assertEqual(msg.onextel_message_id, "carrier-id-custom")

    def test_batch_response_failed_entry_in_middle_only_that_failed_others_correct_ids(self):
        """3. Response with a failed entry in the middle -> only that message FAILED, others keep their own correct IDs."""
        m1 = self._create_message("919876543210")
        m2 = self._create_message("919876543211")
        m3 = self._create_message("919876543212")

        raw_response = [
            {"messageId": "carrier-id-1", "code": 200, "status": "success"},
            {"code": 400, "status": "failed", "message": "Invalid recipient MSISDN"},
            {"messageId": "carrier-id-3", "code": 200, "status": "success"},
        ]

        with patch("messaging.tasks.OneXtelClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.send_messages.return_value = raw_response
            mock_client_cls.return_value = mock_client

            send_batch_messages_task([str(m1.id), str(m2.id), str(m3.id)])

            m1.refresh_from_db()
            m2.refresh_from_db()
            m3.refresh_from_db()

            # m1 and m3 must be SUBMITTED with their respective carrier IDs
            self.assertEqual(m1.status, Message.Status.SUBMITTED)
            self.assertEqual(m1.onextel_message_id, "carrier-id-1")

            self.assertEqual(m3.status, Message.Status.SUBMITTED)
            self.assertEqual(m3.onextel_message_id, "carrier-id-3")

            # m2 must be FAILED, have NO onextel_message_id, and have failure reason
            self.assertEqual(m2.status, Message.Status.FAILED)
            self.assertEqual(m2.onextel_message_id, "")
            self.assertIn("Invalid recipient MSISDN", m2.failure_reason)

    def test_dlr_matched_by_custref1_when_message_id_unknown(self):
        """4. DLR matched by custref1 when messageId is unknown."""
        msg = self._create_message(
            "919876543210",
            status=Message.Status.SUBMITTED,
            onextel_id="",
            custref1="",
        )
        msg.custref1 = str(msg.id)
        msg.save(update_fields=["custref1"])

        # DLR has an unknown messageId from operator, but custref1 matches message.id
        dlr_payload = {
            "messageId": "operator-carrier-999",
            "status": "DELIVERED",
            "custref1": str(msg.id),
        }
        dlr_log = DLRLog.objects.create(payload=dlr_payload)

        process_dlr_task(dlr_log.id)

        msg.refresh_from_db()
        dlr_log.refresh_from_db()

        self.assertEqual(dlr_log.message_id, msg.id)
        self.assertEqual(msg.status, Message.Status.DELIVERED)
        # Real carrier ID from DLR should be populated since it was empty
        self.assertEqual(msg.onextel_message_id, "operator-carrier-999")
        self.assertTrue(dlr_log.processed)

    def test_dlr_phone_fallback_two_candidates_not_matched_logged(self):
        """5. DLR phone fallback with two candidate messages -> not matched, logged, left unprocessed."""
        # Create two submitted messages with same recipient and empty carrier IDs
        msg1 = self._create_message("919876543210", status=Message.Status.SUBMITTED, onextel_id="")
        msg2 = self._create_message("919876543210", status=Message.Status.SUBMITTED, onextel_id="")

        dlr_payload = {
            "messageId": "unknown-carrier-id",
            "recipient": "9876543210",
            "status": "DELIVERED",
        }
        dlr_log = DLRLog.objects.create(payload=dlr_payload)

        process_dlr_task(dlr_log.id)

        dlr_log.refresh_from_db()
        # Must NOT guess: dlr_log should be unprocessed with error message
        self.assertFalse(dlr_log.processed)
        self.assertIn("Ambiguous phone match", dlr_log.error_message)

        # Neither message should have received the carrier ID or status change
        msg1.refresh_from_db()
        msg2.refresh_from_db()
        self.assertEqual(msg1.status, Message.Status.SUBMITTED)
        self.assertEqual(msg1.onextel_message_id, "")
        self.assertEqual(msg2.status, Message.Status.SUBMITTED)
        self.assertEqual(msg2.onextel_message_id, "")

    def test_dlr_matched_via_custref_updates_mismatched_id_and_processes_status(self):
        """6. If matched via custref1 and stored onextel_message_id differs, update stored ID and continue processing."""
        msg = self._create_message(
            "919876543210",
            status=Message.Status.SUBMITTED,
            onextel_id="old-mismatched-id-123",
            custref1="",
        )
        msg.custref1 = str(msg.id)
        msg.save(update_fields=["custref1"])

        dlr_payload = {
            "messageId": "real-operator-id-456",
            "custref1": str(msg.id),
            "status": "DELIVERED",
        }
        dlr_log = DLRLog.objects.create(payload=dlr_payload)

        process_dlr_task(dlr_log.id)

        msg.refresh_from_db()
        dlr_log.refresh_from_db()

        # Stored ID must be updated to the real operator ID
        self.assertEqual(msg.onextel_message_id, "real-operator-id-456")
        # Status must continue processing to DELIVERED
        self.assertEqual(msg.status, Message.Status.DELIVERED)
        self.assertTrue(dlr_log.processed)

    def test_delivered_dlr_updates_campaign_contact_and_delivered_count(self):
        """7. DELIVERED DLR updates CampaignContact status and Campaign.delivered_count (UnboundLocalError regression)."""
        campaign = Campaign.objects.create(
            name="Test Campaign",
            user=self.user,
            sender_profile=self.sender_profile,
            total=1,
            rate_applied=Decimal("0.2500"),
            delivered_count=0,
            failed_count=0,
        )

        msg = self._create_message(
            "919876543210",
            status=Message.Status.SUBMITTED,
            onextel_id="op-carrier-msg-777",
            custref1="",
        )
        msg.campaign_id = campaign.id
        msg.save(update_fields=["campaign_id"])

        contact = CampaignContact.objects.create(
            campaign=campaign,
            number="919876543210",
            status=CampaignContact.Status.DISPATCHED,
            message=msg,
        )

        dlr_payload = {
            "messageId": "op-carrier-msg-777",
            "status": "DELIVERED",
        }
        dlr_log = DLRLog.objects.create(payload=dlr_payload)

        # This should execute without UnboundLocalError and properly update contact + campaign counters
        process_dlr_task(dlr_log.id)

        contact.refresh_from_db()
        campaign.refresh_from_db()
        msg.refresh_from_db()

        self.assertEqual(msg.status, Message.Status.DELIVERED)
        self.assertEqual(contact.status, CampaignContact.Status.DELIVERED)
        self.assertEqual(campaign.delivered_count, 1)
        self.assertEqual(campaign.failed_count, 0)

    def test_reconcile_message_ids_command(self):
        """8. reconcile_message_ids management command dry-run and apply modes."""
        import tempfile
        from django.core.management import call_command
        from io import StringIO

        campaign = Campaign.objects.create(
            name="Reconcile Test Campaign",
            user=self.user,
            sender_profile=self.sender_profile,
            total=2,
            rate_applied=Decimal("0.2500"),
        )

        # Message 1: stored ID differs from operator
        m1 = self._create_message("919876543210", status=Message.Status.SUBMITTED, onextel_id="old-wrong-id")
        m1.campaign_id = campaign.id
        m1.save(update_fields=["campaign_id"])

        c1 = CampaignContact.objects.create(
            campaign=campaign,
            number="919876543210",
            status=CampaignContact.Status.DISPATCHED,
            message=m1,
        )

        # Message 2: missing from operator export
        m2 = self._create_message("919876543211", status=Message.Status.SUBMITTED, onextel_id="portal-only-id")
        m2.campaign_id = campaign.id
        m2.save(update_fields=["campaign_id"])

        # CSV contains m1 with operator ID + extra number not in portal
        csv_content = (
            "recipient,message_id,status\n"
            "9876543210,op-real-id-123,delivered\n"
            "9999999999,op-unknown-id,delivered\n"
        )

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tmp:
            tmp.write(csv_content)
            tmp.flush()
            tmp_path = tmp.name

        try:
            # 1. Test Dry Run (default)
            out_dry = StringIO()
            call_command("reconcile_message_ids", tmp_path, str(campaign.id), stdout=out_dry)
            dry_output = out_dry.getvalue()

            self.assertIn("Mismatched message IDs   : 1", dry_output)
            self.assertIn("Missing in portal        : 1", dry_output)
            self.assertIn("Missing at operator      : 1", dry_output)

            # DB should NOT be changed in dry-run
            m1.refresh_from_db()
            self.assertEqual(m1.onextel_message_id, "old-wrong-id")
            self.assertEqual(m1.status, Message.Status.SUBMITTED)

            # 2. Test Apply
            out_apply = StringIO()
            call_command("reconcile_message_ids", tmp_path, str(campaign.id), "--apply", stdout=out_apply)
            apply_output = out_apply.getvalue()

            self.assertIn("Mismatched message IDs   : 1", apply_output)

            # DB should be updated
            m1.refresh_from_db()
            c1.refresh_from_db()
            self.assertEqual(m1.onextel_message_id, "op-real-id-123")
            self.assertEqual(m1.status, Message.Status.DELIVERED)
            self.assertEqual(c1.status, CampaignContact.Status.DELIVERED)

        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_alert_stuck_submitted_messages_task(self):
        """9. alert_stuck_submitted_messages_task identifies stuck submitted messages."""
        from datetime import timedelta
        from messaging.tasks import alert_stuck_submitted_messages_task

        # Old message (>15 min) in SUBMITTED with empty onextel ID
        old_time = timezone.now() - timedelta(minutes=20)
        m_stuck = self._create_message("919876543210", status=Message.Status.SUBMITTED, onextel_id="")
        m_stuck.submitted_at = old_time
        m_stuck.save(update_fields=["submitted_at"])

        # Recent message (<5 min)
        recent_time = timezone.now() - timedelta(minutes=5)
        m_recent = self._create_message("919876543211", status=Message.Status.SUBMITTED, onextel_id="")
        m_recent.submitted_at = recent_time
        m_recent.save(update_fields=["submitted_at"])

        # Message with confirmed ID
        m_with_id = self._create_message("919876543212", status=Message.Status.SUBMITTED, onextel_id="carrier-id-ok")
        m_with_id.submitted_at = old_time
        m_with_id.save(update_fields=["submitted_at"])

        count = alert_stuck_submitted_messages_task()
        self.assertEqual(count, 1)
