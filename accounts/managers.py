"""Custom managers and queryset mixins for Role Scoping."""
from django.contrib.auth.base_user import BaseUserManager
from django.db import models
from django.db.models import Q


class UserManager(BaseUserManager):
    """Custom user manager supporting username-based authentication."""

    def create_user(self, username=None, email=None, password=None, **extra_fields):
        if not username and email:
            username = email.split("@")[0]
        if not username and "email" in extra_fields:
            username = extra_fields["email"].split("@")[0]
        if not username:
            raise ValueError("The Username field must be set")
        username = username.strip().lower()
        if email:
            email = self.normalize_email(email).lower()
        extra_fields.setdefault("is_active", True)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username=None, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "superadmin")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        if not username and email:
            username = email.split("@")[0]
        if not username:
            username = "admin"

        return self.create_user(username=username, email=email, password=password, **extra_fields)


class RoleScopedQuerySetMixin:
    """QuerySet mixin that filters records according to user roles:

    - Superadmin / Admin: Sees all records
    - Reseller: Sees their own records and records belonging to their clients (parent_reseller=user)
    - Regular User: Sees only their own records
    """

    user_field = "user"

    def for_user(self, user):
        if not user or not user.is_authenticated:
            return self.none()

        # Superadmin, Admin, or Django superuser sees everything
        if getattr(user, "is_superuser", False) or getattr(user, "role", None) in ["superadmin", "admin"]:
            return self.all()

        is_user_model = self.model._meta.model_name.lower() == "user" and self.model._meta.app_label == "accounts"

        # Reseller: sees self + users where parent_reseller == user
        if getattr(user, "role", None) == "reseller":
            if is_user_model:
                return self.filter(Q(id=user.id) | Q(parent_reseller=user))
            else:
                uf = getattr(self, "user_field", "user")
                return self.filter(
                    Q(**{uf: user}) | Q(**{f"{uf}__parent_reseller": user})
                )

        # Standard client user: sees only self / own data
        if is_user_model:
            return self.filter(id=user.id)
        uf = getattr(self, "user_field", "user")
        return self.filter(**{uf: user})


class RoleScopedQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    pass


class RoleScopedManager(models.Manager.from_queryset(RoleScopedQuerySet)):
    """Manager providing .for_user(user) method."""

    def for_user(self, user):
        return self.get_queryset().for_user(user)


class UserQuerySet(RoleScopedQuerySetMixin, models.QuerySet):
    pass


class ScopedUserManager(UserManager.from_queryset(UserQuerySet)):
    """User manager combining custom user creation with role scoping."""

    def for_user(self, user):
        return self.get_queryset().for_user(user)
