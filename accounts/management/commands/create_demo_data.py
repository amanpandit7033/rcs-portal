"""Management command to populate initial demo accounts (1 Admin, 1 Reseller, 2 Users)."""
from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Creates 1 admin, 1 reseller, and 2 users under that reseller."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Setting up demo accounts for RCS Portal..."))

        # 1. Superadmin
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@rcsportal.com",
                "role": User.Role.SUPERADMIN,
                "first_name": "Portal",
                "last_name": "Superadmin",
                "company_name": "RCS Portal HQ",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        admin_user.set_password("admin")
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.role = User.Role.SUPERADMIN
        admin_user.email = "admin@rcsportal.com"
        admin_user.save()
        self.stdout.write(self.style.SUCCESS(f"[OK] Superadmin user created/updated: username='admin' (Password: admin)"))

        # 2. Reseller
        reseller_user, created = User.objects.get_or_create(
            username="reseller",
            defaults={
                "email": "reseller@acme-messaging.com",
                "role": User.Role.RESELLER,
                "first_name": "Rahul",
                "last_name": "Sharma",
                "company_name": "Acme Messaging Solutions",
                "phone": "+919876543210",
                "state": "Maharashtra",
                "GSTIN": "27ABCDE1234F1Z5",
            },
        )
        reseller_user.set_password("reseller")
        reseller_user.role = User.Role.RESELLER
        reseller_user.email = "reseller@acme-messaging.com"
        reseller_user.save()
        self.stdout.write(self.style.SUCCESS(f"[OK] Reseller user created/updated: username='reseller' (Password: reseller)"))

        # 3. User 1 under reseller
        user1, created = User.objects.get_or_create(
            username="user1",
            defaults={
                "email": "user1@alpha-corp.com",
                "role": User.Role.USER,
                "parent_reseller": reseller_user,
                "first_name": "Aarav",
                "last_name": "Patel",
                "company_name": "Alpha Retail Corp",
                "phone": "+919876543211",
                "state": "Delhi",
                "GSTIN": "07ABCDE1234F1Z6",
            },
        )
        user1.set_password("user1")
        user1.parent_reseller = reseller_user
        user1.role = User.Role.USER
        user1.email = "user1@alpha-corp.com"
        user1.save()
        self.stdout.write(self.style.SUCCESS(f"[OK] User 1 created/updated: username='user1' (Password: user1) under reseller"))

        # 4. User 2 under reseller
        user2, created = User.objects.get_or_create(
            username="user2",
            defaults={
                "email": "user2@beta-logistics.com",
                "role": User.Role.USER,
                "parent_reseller": reseller_user,
                "first_name": "Priya",
                "last_name": "Nair",
                "company_name": "Beta Express Logistics",
                "phone": "+919876543212",
                "state": "Karnataka",
                "GSTIN": "29ABCDE1234F1Z7",
            },
        )
        user2.set_password("user2")
        user2.parent_reseller = reseller_user
        user2.role = User.Role.USER
        user2.email = "user2@beta-logistics.com"
        user2.save()
        # 5. Sender Profile (Azmobia_promo) & Wallets
        from decimal import Decimal
        from wallet.models import RatePlan, SenderProfile, Wallet

        default_rates = {
            RatePlan.MessageType.PROMOTIONAL: Decimal("0.2500"),
            RatePlan.MessageType.TRANSACTIONAL: Decimal("0.2000"),
            RatePlan.MessageType.OTP: Decimal("0.1500"),
        }

        for u in [admin_user, reseller_user, user1, user2]:
            Wallet.objects.get_or_create(user=u, defaults={"balance": Decimal("10000.0000")})
            for m_type, rate_val in default_rates.items():
                RatePlan.objects.get_or_create(
                    user=u,
                    message_type=m_type,
                    defaults={"rate": rate_val, "cost_rate": Decimal("0.1000")},
                )
            sp, sp_created = SenderProfile.objects.get_or_create(
                user=u,
                sender_id="Azmobia_promo",
                defaults={
                    "sender_profile_name": "Azmobia_promo",
                    "is_active": True,
                },
            )
            if not sp.is_active or sp.sender_profile_name != "Azmobia_promo":
                sp.sender_profile_name = "Azmobia_promo"
                sp.is_active = True
                sp.save()

        self.stdout.write(self.style.SUCCESS("[OK] Configured Sender Profile 'Azmobia_promo', Rate Plans, and Wallets for all accounts."))
        self.stdout.write(self.style.SUCCESS("\nDemo accounts ready! You can now log into any tier."))
