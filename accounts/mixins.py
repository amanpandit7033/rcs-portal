"""View mixins for Role-Based Access Control and QuerySet scoping."""
from typing import List
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied


class RoleRequiredMixin(LoginRequiredMixin):
    """Mixin enforcing that the authenticated user must have one of the allowed roles.

    Superusers are always allowed unless explicitly overridden.
    """

    allowed_roles: List[str] = []

    def get_allowed_roles(self) -> List[str]:
        return self.allowed_roles

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        user_role = getattr(request.user, "role", None)
        if user_role not in self.get_allowed_roles():
            raise PermissionDenied("You do not have permission to view this page.")

        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    """Allows only Superadmin / Admin users (or superusers)."""
    allowed_roles = ["superadmin", "admin"]


class ResellerRequiredMixin(RoleRequiredMixin):
    """Allows Reseller users (or superadmin/admin)."""
    allowed_roles = ["reseller", "superadmin", "admin"]


class UserRequiredMixin(RoleRequiredMixin):
    """Allows standard Users, Resellers, and Superadmins/Admins."""
    allowed_roles = ["user", "reseller", "superadmin", "admin"]


class RoleScopedViewMixin:
    """View mixin that automatically scopes get_queryset() to request.user."""

    def get_queryset(self):
        qs = super().get_queryset()
        if hasattr(qs, "for_user"):
            return qs.for_user(self.request.user)
        return qs
