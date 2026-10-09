"""Template tags and filters for wallet and INR presentation."""
from django import template
from wallet.utils import format_inr as utils_format_inr

register = template.Library()


@register.filter(name="format_inr")
def format_inr_filter(value, decimal_places=2):
    """Template filter to render amounts in INR with Indian grouping."""
    return utils_format_inr(value, decimal_places=int(decimal_places))
