"""Views for analytics, financial margin computation, DLR inspection, and streaming CSV export."""
from decimal import Decimal
from datetime import timedelta
import logging
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from accounts.mixins import AdminRequiredMixin, ResellerRequiredMixin, RoleRequiredMixin
from campaigns.models import Campaign, CampaignContact
from messaging.models import DLRLog, Message
from messaging.tasks import process_dlr_task
from wallet.models import RatePlan, WalletTransaction
from .csv_stream import stream_csv_response

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------------
# 1. Message Logs Streaming CSV Export (All Roles)
# ---------------------------------------------------------------------------

class MessageLogExportCSVView(RoleRequiredMixin, View):
    """Stream entire filtered message log as a CSV attachment without memory load."""

    allowed_roles = ["admin", "reseller", "user"]

    def get(self, request):
        qs = Message.objects.for_user(request.user).select_related(
            "user", "sender_profile", "template"
        ).order_by("-created_at")

        # Apply same filters as MessageLogView
        q = request.GET.get("q")
        status = request.GET.get("status")
        message_type = request.GET.get("message_type")
        date_str = request.GET.get("date")

        if q:
            qs = qs.filter(user__username__icontains=q)
        if status:
            qs = qs.filter(status=status)
        if message_type:
            qs = qs.filter(message_type=message_type)
        if date_str:
            qs = qs.filter(created_at__date=date_str)

        is_admin = getattr(request.user, "is_admin", False)

        header = []
        if is_admin:
            header.extend(["Message ID", "Carrier Message ID"])
        header.extend([
            "Recipient",
            "Sender Profile",
            "Message Intent",
            "Content Architecture",
            "Template Name",
            "Status",
            "Cost (INR)",
            "Is Refunded",
            "Failure Reason",
            "Dispatched At",
            "Delivered At",
            "Read At",
            "User Email",
        ])

        def format_row(m):
            row = []
            if is_admin:
                row.extend([str(m.id), m.onextel_message_id or ""])
            row.extend([
                f"{m.prefix}{m.recipient}",
                m.sender_profile.sender_profile_name if m.sender_profile else "",
                m.message_type,
                m.get_content_type_display(),
                m.template.name if m.template else "",
                m.get_status_display(),
                str(m.cost),
                "Yes" if m.is_refunded else "No",
                m.failure_reason,
                m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
                m.delivered_at.strftime("%Y-%m-%d %H:%M:%S") if m.delivered_at else "",
                m.read_at.strftime("%Y-%m-%d %H:%M:%S") if m.read_at else "",
                m.user.email if m.user else "",
            ])
            return row

        filename = f"rcs_messages_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return stream_csv_response(
            filename=filename,
            header=header,
            queryset_iterator=qs.iterator(chunk_size=1000),
            row_formatter=format_row,
        )


# ---------------------------------------------------------------------------
# 2. User Usage Report (Messages x Rate per Type per Day)
# ---------------------------------------------------------------------------

class UserUsageReportView(RoleRequiredMixin, View):
    """Daily breakdown of messages and costs grouped by message intent."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "reports/user_usage_report.html"

    def get(self, request):
        user = request.user
        days = int(request.GET.get("days", 30))
        since_date = timezone.now() - timedelta(days=days)

        base_qs = Message.objects.for_user(user).filter(created_at__gte=since_date)

        # Aggregate by date and message_type
        daily_breakdown = (
            base_qs.annotate(date=TruncDate("created_at"))
            .values("date", "message_type")
            .annotate(
                total_count=Count("id"),
                delivered_count=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
                failed_count=Count("id", filter=Q(status=Message.Status.FAILED)),
                total_cost=Sum("cost"),
            )
            .order_by("-date", "message_type")
        )

        if request.GET.get("export") == "csv":
            header = ["Date", "Message Intent", "Total Count", "Delivered Count", "Failed Count", "Total Cost (INR)"]

            def format_row(row):
                return [
                    str(row["date"]),
                    row["message_type"],
                    row["total_count"],
                    row["delivered_count"],
                    row["failed_count"],
                    str(row["total_cost"] or "0.0000"),
                ]

            filename = f"usage_report_{timezone.now().strftime('%Y%m%d')}.csv"
            return stream_csv_response(
                filename=filename,
                header=header,
                queryset_iterator=daily_breakdown.iterator(chunk_size=500),
                row_formatter=format_row,
            )

        # Summary totals
        totals = base_qs.aggregate(
            all_messages=Count("id"),
            delivered=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
            failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            spent=Sum("cost"),
        )

        return render(
            request,
            self.template_name,
            {
                "daily_breakdown": daily_breakdown,
                "totals": totals,
                "selected_days": days,
                "page_title": "Usage Analytics Report",
            },
        )


# ---------------------------------------------------------------------------
# 3. Reseller Client Usage & Margin Report
# ---------------------------------------------------------------------------

class ResellerMarginReportView(ResellerRequiredMixin, View):
    """Client-wise usage, selling revenue, underlying cost, and profit margin for Resellers."""

    template_name = "reports/reseller_margin_report.html"

    def get(self, request):
        reseller = request.user
        clients = User.objects.filter(parent_reseller=reseller)

        # Pre-fetch reseller rate plans (cost basis for reseller)
        reseller_rates = {
            rp.message_type: rp.cost_rate
            for rp in RatePlan.objects.filter(user=reseller)
        }

        client_stats = []
        overall_sell_total = Decimal("0.0000")
        overall_cost_total = Decimal("0.0000")
        overall_margin_total = Decimal("0.0000")
        overall_messages_count = 0

        for client_user in clients:
            client_msgs = Message.objects.filter(user=client_user)
            aggregates = client_msgs.aggregate(
                total_count=Count("id"),
                delivered_count=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
                failed_count=Count("id", filter=Q(status=Message.Status.FAILED)),
                revenue=Sum("cost"),
            )

            total_count = aggregates["total_count"] or 0
            revenue = aggregates["revenue"] or Decimal("0.0000")

            # Calculate reseller underlying cost based on message types
            client_cost = Decimal("0.0000")
            for m_type, cost_rate in reseller_rates.items():
                m_count = client_msgs.filter(message_type=m_type).count()
                client_cost += Decimal(m_count) * cost_rate

            margin = revenue - client_cost

            overall_messages_count += total_count
            overall_sell_total += revenue
            overall_cost_total += client_cost
            overall_margin_total += margin

            client_stats.append({
                "client": client_user,
                "total_count": total_count,
                "delivered_count": aggregates["delivered_count"] or 0,
                "failed_count": aggregates["failed_count"] or 0,
                "revenue": revenue,
                "cost": client_cost,
                "margin": margin,
                "margin_pct": (margin / revenue * 100) if revenue > 0 else 0,
            })

        if request.GET.get("export") == "csv":
            header = ["Client Email", "Company", "Total Messages", "Delivered", "Failed", "Selling Revenue (INR)", "Underlying Cost (INR)", "Net Margin (INR)", "Margin %"]

            def format_row(r):
                return [
                    r["client"].email,
                    r["client"].company_name,
                    r["total_count"],
                    r["delivered_count"],
                    r["failed_count"],
                    str(r["revenue"]),
                    str(r["cost"]),
                    str(r["margin"]),
                    f"{r['margin_pct']:.2f}%",
                ]

            filename = f"reseller_margin_report_{timezone.now().strftime('%Y%m%d')}.csv"
            return stream_csv_response(
                filename=filename,
                header=header,
                queryset_iterator=client_stats,
                row_formatter=format_row,
            )

        return render(
            request,
            self.template_name,
            {
                "client_stats": client_stats,
                "overall_messages_count": overall_messages_count,
                "overall_sell_total": overall_sell_total,
                "overall_cost_total": overall_cost_total,
                "overall_margin_total": overall_margin_total,
                "overall_margin_pct": (overall_margin_total / overall_sell_total * 100) if overall_sell_total > 0 else 0,
                "page_title": "Reseller Client Usage & Margin Report",
            },
        )



# ---------------------------------------------------------------------------
# 5. Admin DLRLog Viewer & Live Reprocessor
# ---------------------------------------------------------------------------

class AdminDLRLogListView(AdminRequiredMixin, View):
    """Inspect raw carrier webhook receipts with reprocess and simulation capabilities."""

    template_name = "reports/admin_dlr_logs.html"

    def get(self, request):
        qs = DLRLog.objects.select_related("message").order_by("-received_at")

        processed_filter = request.GET.get("processed")
        if processed_filter == "true":
            qs = qs.filter(processed=True)
        elif processed_filter == "false":
            qs = qs.filter(processed=False)

        q = request.GET.get("q")
        if q:
            qs = qs.filter(
                Q(onextel_message_id__icontains=q)
                | Q(status_extracted__icontains=q)
                | Q(source_ip__icontains=q)
            )

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        return render(
            request,
            self.template_name,
            {
                "dlr_logs": page_obj,
                "page_title": "Carrier DLR Webhook Logs",
            },
        )

    def post(self, request):
        action = request.POST.get("action")

        if action == "reprocess":
            dlr_id = request.POST.get("dlr_id")
            dlr_log = get_object_or_404(DLRLog, id=dlr_id)
            process_dlr_task(dlr_log.id)
            messages.success(request, f"DLRLog #{dlr_id} reprocessed successfully.")

        elif action == "simulate":
            carrier_id = request.POST.get("carrier_id", "").strip()
            sim_status = request.POST.get("status", "delivered").strip()
            reason = request.POST.get("reason", "").strip()

            if not carrier_id:
                messages.error(request, "Carrier Message ID is required for DLR simulation.")
                return redirect("reports:admin_dlr_logs")

            payload = {
                "messageId": carrier_id,
                "status": sim_status.upper(),
                "timestamp": timezone.now().isoformat(),
                "channel": "RCS",
            }
            if sim_status == "failed":
                payload["reason"] = reason or "Manual simulated carrier failure"

            log = DLRLog.objects.create(
                payload=payload,
                source_ip="127.0.0.1",
                processed=False,
            )
            process_dlr_task(log.id)
            messages.success(request, f"Simulated DLR #{log.id} created and dispatched.")

        return redirect("reports:admin_dlr_logs")


# ---------------------------------------------------------------------------
# 5. Admin Campaign Reports (All Campaigns Overview, View & Update)
# ---------------------------------------------------------------------------

class AdminCampaignReportListView(AdminRequiredMixin, View):
    """Admin-only overview of all campaigns across all clients with view and update capabilities."""

    template_name = "reports/admin_campaign_reports.html"

    def get(self, request):
        qs = (
            Campaign.objects.all()
            .select_related("user", "sender_profile", "template")
            .order_by("-created_at")
        )

        q = request.GET.get("q", "").strip()
        status = request.GET.get("status", "").strip()
        user_id = request.GET.get("user_id", "").strip()
        from_date = request.GET.get("from_date", "").strip()
        to_date = request.GET.get("to_date", "").strip()
        date_str = request.GET.get("date", "").strip()

        if q:
            qs = qs.filter(user__username__icontains=q)
        if status:
            qs = qs.filter(status=status)
        if user_id:
            qs = qs.filter(user_id=user_id)
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)
        if date_str:
            qs = qs.filter(created_at__date=date_str)

        # Overview statistics
        total_campaigns = Campaign.objects.count()
        running_campaigns = Campaign.objects.filter(status=Campaign.Status.RUNNING).count()
        completed_campaigns = Campaign.objects.filter(status=Campaign.Status.COMPLETED).count()
        total_recipients = Campaign.objects.aggregate(total=Sum("valid_count"))["total"] or 0

        paginator = Paginator(qs, 20)
        page_obj = paginator.get_page(request.GET.get("page", 1))

        # Users list for filter dropdown and autocomplete
        users = User.objects.filter(role__in=["user", "reseller"]).order_by("username")
        candidate_usernames = list(users.values_list("username", flat=True))

        return render(
            request,
            self.template_name,
            {
                "campaigns": page_obj,
                "users": users,
                "candidate_usernames": candidate_usernames,
                "selected_status": status,
                "selected_user": user_id,
                "from_date": from_date,
                "to_date": to_date,
                "selected_date": date_str,
                "search_query": q,
                "total_campaigns": total_campaigns,
                "running_campaigns": running_campaigns,
                "completed_campaigns": completed_campaigns,
                "total_recipients": total_recipients,
                "status_choices": Campaign.Status.choices,
                "page_title": "Campaign Reports",
            },
        )

    def post(self, request):
        """Action handler for bulk updating campaign message statuses."""
        campaign_id = request.POST.get("campaign_id")
        campaign = get_object_or_404(Campaign, id=campaign_id)

        try:
            count = int(request.POST.get("count", 0))
        except (ValueError, TypeError):
            count = 0

        from_status = request.POST.get("from_status", "").strip().lower()
        to_status = request.POST.get("to_status", "").strip().lower()
        date_str = request.POST.get("date", "").strip()
        from_time_str = request.POST.get("from_time", "").strip()
        to_time_str = request.POST.get("to_time", "").strip()

        if count <= 0:
            messages.error(request, "Count must be a valid positive number.")
            return redirect("reports:admin_campaign_reports")

        # 1. Filter matching messages based on from_status
        qs = Message.objects.filter(campaign=campaign)

        if from_status == "actual_submitted":
            qs = qs.filter(status=Message.Status.SUBMITTED).exclude(custref6="LOW_PRICE_SKIPPED")
        elif from_status == "failed":
            qs = qs.filter(status=Message.Status.FAILED)
        elif from_status == "skipped":
            qs = qs.filter(custref6="LOW_PRICE_SKIPPED")
        else:
            messages.error(request, f"Invalid 'From Status' selected: {from_status}")
            return redirect("reports:admin_campaign_reports")

        # 2. Determine target status
        if to_status == "delivered":
            target_msg_status = Message.Status.DELIVERED
            target_contact_status = CampaignContact.Status.DELIVERED
        elif to_status == "failed":
            target_msg_status = Message.Status.FAILED
            target_contact_status = CampaignContact.Status.FAILED
        elif to_status == "submitted":
            target_msg_status = Message.Status.SUBMITTED
            target_contact_status = CampaignContact.Status.DISPATCHED
        else:
            messages.error(request, f"Invalid 'To Status' selected: {to_status}")
            return redirect("reports:admin_campaign_reports")

        # Exclude messages that already have target_status to avoid redundant updates
        qs = qs.exclude(status=target_msg_status)

        all_eligible_ids = list(qs.values_list("id", flat=True))
        total_matching = len(all_eligible_ids)
        if total_matching == 0:
            messages.warning(
                request,
                f"No messages found matching '{from_status.replace('_', ' ').title()}' that need update to '{to_status.title()}' in campaign '{campaign.name}'.",
            )
            return redirect("reports:admin_campaign_reports")

        # Pick totally randomly across the eligible numbers (never sequential one-by-one)
        import random
        if count >= total_matching:
            target_ids = all_eligible_ids
        else:
            target_ids = random.sample(all_eligible_ids, count)
        actual_update_count = len(target_ids)

        # 3. Calculate delivery / event timestamps if delivered
        now = timezone.now()
        start_ts = now
        end_ts = now

        if date_str:
            try:
                from datetime import datetime
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
                if from_time_str:
                    t_from = datetime.strptime(from_time_str, "%H:%M").time()
                else:
                    t_from = datetime.strptime("00:00", "%H:%M").time()

                if to_time_str:
                    t_to = datetime.strptime(to_time_str, "%H:%M").time()
                else:
                    t_to = datetime.strptime("23:59", "%H:%M").time()

                tz = timezone.get_current_timezone()
                start_ts = timezone.make_aware(datetime.combine(d, t_from), tz)
                end_ts = timezone.make_aware(datetime.combine(d, t_to), tz)
                if end_ts < start_ts:
                    end_ts, start_ts = start_ts, end_ts
            except Exception as parse_err:
                logger.warning("Error parsing date/time for admin update: %s", parse_err)
                start_ts = now
                end_ts = now

        # 4. Perform update on Message models
        if to_status == "delivered":
            span_seconds = int((end_ts - start_ts).total_seconds())
            if span_seconds > 0:
                messages_to_update = list(Message.objects.filter(id__in=target_ids))
                for msg_obj in messages_to_update:
                    random_offset = random.randint(0, span_seconds)
                    msg_obj.status = target_msg_status
                    msg_obj.delivered_at = start_ts + timedelta(seconds=random_offset)
                    msg_obj.failure_reason = ""
                Message.objects.bulk_update(
                    messages_to_update,
                    ["status", "delivered_at", "failure_reason"],
                    batch_size=500,
                )
            else:
                Message.objects.filter(id__in=target_ids).update(
                    status=target_msg_status,
                    delivered_at=end_ts,
                    failure_reason="",
                )
        elif to_status == "failed":
            Message.objects.filter(id__in=target_ids).update(
                status=target_msg_status,
                failure_reason="Failed - Operator Delivery Failure",
                delivered_at=None,
            )
        elif to_status == "submitted":
            Message.objects.filter(id__in=target_ids).update(
                status=target_msg_status,
                delivered_at=None,
                failure_reason="",
            )

        # 5. Update matching CampaignContact rows
        CampaignContact.objects.filter(
            campaign=campaign,
            message_id__in=target_ids,
        ).update(
            status=target_contact_status,
            error_message="" if to_status != "failed" else "Failed - Operator Delivery Failure",
        )

        # 6. Refresh campaign processed counters
        campaign.processed_count = CampaignContact.objects.filter(
            campaign=campaign,
            status__in=[
                CampaignContact.Status.DISPATCHED,
                CampaignContact.Status.DELIVERED,
                CampaignContact.Status.FAILED,
            ],
        ).count()
        campaign.save(update_fields=["processed_count", "updated_at"])

        # 7. Sync MIS Billing for updated campaign
        try:
            from .models import sync_user_daily_mis
            if campaign.user_id:
                c_date = campaign.created_at.date() if campaign.created_at else timezone.now().date()
                sync_user_daily_mis(user_id=campaign.user_id, date=c_date)
        except Exception as sync_err:
            logger.warning("Failed to sync MIS billing after status update: %s", sync_err)

        messages.success(
            request,
            f"Successfully updated {actual_update_count} message(s) from '{from_status.replace('_', ' ').title()}' to '{to_status.title()}' in '{campaign.name}'.",
        )
        return redirect("reports:admin_campaign_reports")


# ---------------------------------------------------------------------------
# 5. MIS Billing View & Export
# ---------------------------------------------------------------------------

class MISBillingView(RoleRequiredMixin, View):
    """Daily MIS Billing report tracking per-user sent, delivered, read, failed, and cost."""

    allowed_roles = ["admin", "reseller", "user"]
    template_name = "reports/mis_billing.html"

    def get(self, request):
        user = request.user
        from .models import MISBilling, sync_user_daily_mis

        # Automatically sync today's active counts
        try:
            sync_user_daily_mis(
                user_id=None if getattr(user, "is_admin", False) else user.id,
                date=timezone.now().date(),
            )
        except Exception as e:
            logger.warning("Auto sync MIS error: %s", e)

        qs = MISBilling.objects.for_user(user).select_related("user").order_by("-date", "-total_cost")

        username = request.GET.get("username", "").strip()
        from_date = request.GET.get("from_date", "").strip()
        to_date = request.GET.get("to_date", "").strip()
        user_belonging = request.GET.get("user_belonging", "").strip() in ["1", "true", "on", "yes"]

        # Filter by Username & User Belonging Checkbox
        if username:
            target_user = User.objects.filter(username__iexact=username).first()
            if user_belonging:
                if target_user:
                    # Find all users belonging to this reseller (direct and indirect)
                    belonging_ids = list(
                        User.objects.filter(
                            Q(parent_reseller=target_user) | Q(parent_reseller__parent_reseller=target_user)
                        ).values_list("id", flat=True)
                    )
                    # Skip the reseller's own MIS count, show only belonging users
                    qs = qs.filter(user_id__in=belonging_ids).exclude(user_id=target_user.id)
                else:
                    # Search by parent reseller username
                    qs = qs.filter(user__parent_reseller__username__iexact=username)
            else:
                qs = qs.filter(user__username__icontains=username)
        elif user_belonging:
            # Checkbox selected with no username entered
            if getattr(user, "is_reseller", False):
                # Reseller skipping their own count, showing only their clients
                qs = qs.filter(user__parent_reseller=user).exclude(user_id=user.id)
            elif getattr(user, "is_admin", False):
                # Admin skipping reseller-level records, showing client accounts
                qs = qs.filter(user__role=User.Role.USER)

        # Date range filtering
        if from_date:
            qs = qs.filter(date__gte=from_date)
        if to_date:
            qs = qs.filter(date__lte=to_date)

        # Handle CSV Export if requested
        if request.GET.get("export") == "csv":
            header = ["Date", "Username", "Company Name", "Role", "Sent", "Delivered", "Read", "Failed", "Cost (INR)"]

            def format_row(r):
                return [
                    r.date.strftime("%Y-%m-%d") if r.date else "",
                    r.user.username if r.user else "",
                    r.user.company_name if r.user else "",
                    r.user.get_role_display() if r.user and hasattr(r.user, "get_role_display") else (r.user.role if r.user else ""),
                    r.sent_count,
                    r.delivered_count,
                    r.read_count,
                    r.failed_count,
                    f"{r.total_cost:.4f}",
                ]

            filename = f"mis_billing_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"
            return stream_csv_response(
                filename=filename,
                header=header,
                queryset_iterator=qs.iterator(chunk_size=500),
                row_formatter=format_row,
            )

        # Aggregate totals for filtered records
        summary = qs.aggregate(
            total_sent=Sum("sent_count"),
            total_delivered=Sum("delivered_count"),
            total_read=Sum("read_count"),
            total_failed=Sum("failed_count"),
            total_cost=Sum("total_cost"),
        )
        total_sent = summary["total_sent"] or 0
        total_delivered = summary["total_delivered"] or 0
        total_read = summary["total_read"] or 0
        total_failed = summary["total_failed"] or 0
        total_cost = summary["total_cost"] or Decimal("0.0000")

        delivery_rate = (total_delivered / total_sent * 100) if total_sent > 0 else 0

        # Autocomplete candidate usernames for filter datalist
        if getattr(user, "is_admin", False):
            candidate_users = User.objects.filter(is_active=True).values("username", "company_name", "role").order_by("username")
        elif getattr(user, "is_reseller", False):
            candidate_users = User.objects.filter(
                Q(id=user.id) | Q(parent_reseller=user)
            ).values("username", "company_name", "role").order_by("username")
        else:
            candidate_users = [{"username": user.username, "company_name": user.company_name, "role": user.role}]

        paginator = Paginator(qs, 25)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        return render(
            request,
            self.template_name,
            {
                "page_obj": page_obj,
                "summary": {
                    "total_sent": total_sent,
                    "total_delivered": total_delivered,
                    "total_read": total_read,
                    "total_failed": total_failed,
                    "total_cost": total_cost,
                    "delivery_rate": delivery_rate,
                },
                "username_query": username,
                "from_date": from_date,
                "to_date": to_date,
                "user_belonging": user_belonging,
                "candidate_users": candidate_users,
            },
        )


