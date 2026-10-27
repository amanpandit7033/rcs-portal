"""Context processors for wallet balance and active impersonation sessions."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from .models import Wallet
from .utils import format_inr

User = get_user_model()


def wallet_context(request):
    """Provide wallet balance and impersonation state to all templates."""
    context = {
        "user_wallet_balance": "₹ 0.00",
        "user_wallet_raw_balance": Decimal("0.0000"),
        "impersonator_user": None,
    }

    if not request.user.is_authenticated:
        return context

    # 1. User wallet balance
    try:
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        context["user_wallet_balance"] = format_inr(wallet.balance)
        context["user_wallet_raw_balance"] = wallet.balance
    except Exception:
        pass

    # 2. Check for active impersonation session stack
    stack = list(request.session.get("_impersonator_stack", []))
    if not stack and request.session.get("_impersonator_user_id"):
        stack = [request.session.get("_impersonator_user_id")]

    if stack:
        try:
            prev_id = stack[-1]
            impersonator = User.objects.filter(id=prev_id).first()
            if impersonator:
                context["impersonator_user"] = impersonator

            root_id = stack[0]
            root_user = User.objects.filter(id=root_id).first()
            if root_user:
                context["original_admin_user"] = root_user

            context["impersonation_depth"] = len(stack)
            context["is_nested_impersonation"] = len(stack) > 1
        except Exception:
            pass

    return context
