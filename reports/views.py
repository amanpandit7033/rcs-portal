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
            qs = qs.filter(
                models.Q(recipient__icontains=q)
                | models.Q(id__icontains=q)
                | models.Q(onextel_message_id__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)
        if message_type:
            qs = qs.filter(message_type=message_type)
        if date_str:
            qs = qs.filter(created_at__date=date_str)

        header = [
            "Message ID",
            "OneXtel Carrier ID",
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
        ]

        def format_row(m):
            return [
                str(m.id),
                m.onextel_message_id or "",
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
            ]

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
# 4. Admin Overview (System-wide Traffic, DLR Success Rate & Failure Breakdown)
# ---------------------------------------------------------------------------

class AdminOverviewReportView(AdminRequiredMixin, View):
    """Platform-wide operational telemetry, DLR delivery rate, and error analytics."""

    template_name = "reports/admin_overview_report.html"

    def get(self, request):
        days = int(request.GET.get("days", 30))
        since_date = timezone.now() - timedelta(days=days)
        base_qs = Message.objects.filter(created_at__gte=since_date)

        # Status counts
        status_counts = base_qs.aggregate(
            total=Count("id"),
            queued=Count("id", filter=Q(status=Message.Status.QUEUED)),
            submitted=Count("id", filter=Q(status=Message.Status.SUBMITTED)),
            sent=Count("id", filter=Q(status=Message.Status.SENT)),
            delivered=Count("id", filter=Q(status=Message.Status.DELIVERED)),
            read=Count("id", filter=Q(status=Message.Status.READ)),
            failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            total_revenue=Sum("cost"),
        )

        total_msgs = status_counts["total"] or 0
        successful_dlr = (status_counts["delivered"] or 0) + (status_counts["read"] or 0)
        success_rate = (successful_dlr / total_msgs * 100) if total_msgs > 0 else 0.0

        # Daily revenue and volume
        daily_traffic = (
            base_qs.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                count=Count("id"),
                revenue=Sum("cost"),
                delivered=Count("id", filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ])),
                failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            )
            .order_by("-date")
        )

        # Failure reasons breakdown
        failure_reasons = (
            base_qs.filter(status=Message.Status.FAILED)
            .exclude(failure_reason="")
            .values("failure_reason")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        return render(
            request,
            self.template_name,
            {
                "status_counts": status_counts,
                "success_rate": round(success_rate, 2),
                "daily_traffic": daily_traffic,
                "failure_reasons": failure_reasons,
                "selected_days": days,
                "page_title": "Admin Platform Intelligence & DLR Telemetry",
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
                "page_title": "OneXtel DLR Webhook Logs",
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
