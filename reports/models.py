"""Reports models for daily MIS billing and analytics."""
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from accounts.managers import RoleScopedQuerySetMixin


class MISBillingQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    user_field = "user"


class MISBillingManager(models.Manager.from_queryset(MISBillingQuerySet)):
    def for_user(self, user):
        return self.get_queryset().for_user(user)


class MISBilling(models.Model):
    """Daily aggregated MIS billing record per user tracking message throughput and costs."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mis_billing_records",
        help_text="User/account associated with these counts",
    )
    date = models.DateField(
        "Billing Date",
        db_index=True,
        help_text="Date of message activity",
    )
    sent_count = models.PositiveIntegerField(
        "Total Sent",
        default=0,
        help_text="Total messages dispatched/sent on this date",
    )
    delivered_count = models.PositiveIntegerField(
        "Delivered",
        default=0,
        help_text="Messages delivered or read on this date",
    )
    read_count = models.PositiveIntegerField(
        "Read",
        default=0,
        help_text="Messages confirmed read on this date",
    )
    failed_count = models.PositiveIntegerField(
        "Failed",
        default=0,
        help_text="Messages permanently failed on this date",
    )
    total_cost = models.DecimalField(
        "Total Cost (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Total billable cost debited for messages dispatched on this date",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MISBillingManager()

    class Meta:
        verbose_name = "MIS Billing Record"
        verbose_name_plural = "MIS Billing Records"
        unique_together = ("user", "date")
        ordering = ["-date", "-total_cost"]
        indexes = [
            models.Index(fields=["date", "user"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.date} (Sent: {self.sent_count}, Cost: ₹{self.total_cost})"


def sync_user_daily_mis(user_id=None, date=None):
    """
    Synchronizes MISBilling records from the Message table into the dedicated MISBilling table.
    Can be scoped to a single user and date, or run globally to sync all message history.
    """
    from messaging.models import Message

    qs = Message.objects.all()
    if user_id:
        qs = qs.filter(user_id=user_id)
    if date:
        qs = qs.filter(created_at__date=date)

    aggregates = (
        qs.annotate(activity_date=TruncDate("created_at"))
        .values("user_id", "activity_date")
        .annotate(
            sent_count=Count("id"),
            delivered_count=Count(
                "id",
                filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ]),
            ),
            read_count=Count("id", filter=Q(status=Message.Status.READ)),
            failed_count=Count("id", filter=Q(status=Message.Status.FAILED)),
            total_cost=Sum("cost"),
        )
    )

    for item in aggregates:
        u_id = item["user_id"]
        act_date = item["activity_date"]
        if not u_id or not act_date:
            continue
        MISBilling.objects.update_or_create(
            user_id=u_id,
            date=act_date,
            defaults={
                "sent_count": item["sent_count"] or 0,
                "delivered_count": item["delivered_count"] or 0,
                "read_count": item["read_count"] or 0,
                "failed_count": item["failed_count"] or 0,
                "total_cost": item["total_cost"] or Decimal("0.0000"),
            },
        )


class LiveStatusQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    user_field = "user"


class LiveStatusManager(models.Manager.from_queryset(LiveStatusQuerySet)):
    def for_user(self, user):
        return self.get_queryset().for_user(user)


class LiveStatus(models.Model):
    """Daily per-user live tracking table for submissions, delivery acknowledgments, reads, failures, and cost."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="live_status_records",
        help_text="User/account associated with these counts",
    )
    date = models.DateField(
        "Status Date",
        db_index=True,
        help_text="Date of activity",
    )
    total_submission = models.PositiveIntegerField(
        "Total Submission",
        default=0,
        help_text="Total messages dispatched/submitted on this date",
    )
    total_delivered = models.PositiveIntegerField(
        "Total Delivered",
        default=0,
        help_text="Messages delivered or read on this date",
    )
    total_read = models.PositiveIntegerField(
        "Total Read",
        default=0,
        help_text="Messages confirmed read on this date",
    )
    total_failed = models.PositiveIntegerField(
        "Total Failed",
        default=0,
        help_text="Messages permanently failed on this date",
    )
    total_cost = models.DecimalField(
        "Total Cost (INR)",
        max_digits=14,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Total billable cost debited for messages on this date",
    )
    last_sent_at = models.DateTimeField(
        "Last Activity At",
        null=True,
        blank=True,
        help_text="Timestamp of the most recent message dispatched on this date",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LiveStatusManager()

    class Meta:
        verbose_name = "Live Status Record"
        verbose_name_plural = "Live Status Records"
        unique_together = ("user", "date")
        ordering = ["-date", "-total_submission"]
        indexes = [
            models.Index(fields=["date", "user"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.date} (Submissions: {self.total_submission}, Cost: ₹{self.total_cost})"


def sync_live_status(user_id=None, date=None, from_date=None, to_date=None):
    """
    Synchronizes LiveStatus records from the Message table into the dedicated LiveStatus table.
    Can be scoped to a single user, a single date, or a date range (from_date to to_date).
    """
    from messaging.models import Message
    from django.db.models import Max

    qs = Message.objects.all()
    if user_id:
        qs = qs.filter(user_id=user_id)
    if date:
        qs = qs.filter(created_at__date=date)
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)

    aggregates = (
        qs.annotate(activity_date=TruncDate("created_at"))
        .values("user_id", "activity_date")
        .annotate(
            total_submission=Count("id"),
            total_delivered=Count(
                "id",
                filter=Q(status__in=[Message.Status.DELIVERED, Message.Status.READ]),
            ),
            total_read=Count("id", filter=Q(status=Message.Status.READ)),
            total_failed=Count("id", filter=Q(status=Message.Status.FAILED)),
            total_cost=Sum("cost"),
            last_sent_at=Max("created_at"),
        )
    )

    for item in aggregates:
        u_id = item["user_id"]
        act_date = item["activity_date"]
        if not u_id or not act_date:
            continue
        LiveStatus.objects.update_or_create(
            user_id=u_id,
            date=act_date,
            defaults={
                "total_submission": item["total_submission"] or 0,
                "total_delivered": item["total_delivered"] or 0,
                "total_read": item["total_read"] or 0,
                "total_failed": item["total_failed"] or 0,
                "total_cost": item["total_cost"] or Decimal("0.0000"),
                "last_sent_at": item["last_sent_at"],
            },
        )

