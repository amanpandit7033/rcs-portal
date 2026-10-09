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

    # 2. Check for active reseller impersonation session
    impersonator_id = request.session.get("_impersonator_user_id")
    if impersonator_id:
        try:
            impersonator = User.objects.filter(id=impersonator_id).first()
            if impersonator:
                context["impersonator_user"] = impersonator
        except Exception:
            pass

    return context
