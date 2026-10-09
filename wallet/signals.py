"""Signals for the wallet application."""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Wallet


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    """Ensure every user account has an associated wallet immediately upon creation."""
    if created:
        Wallet.objects.get_or_create(user=instance)
