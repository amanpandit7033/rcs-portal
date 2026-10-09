"""Currency and number formatting utilities for INR (Indian Rupee)."""
from decimal import Decimal
from typing import Union


def format_inr(value: Union[Decimal, float, int, str, None], decimal_places: int = 2) -> str:
    """Format a monetary amount in Indian Rupee format with standard Indian digit grouping:

    e.g. 1234567.89 -> "₹ 12,34,567.89"
         500.00 -> "₹ 500.00"
    """
    if value is None or value == "":
        return "₹ 0.00"

    try:
        dec = Decimal(str(value))
    except Exception:
        return "₹ 0.00"

    sign = "-" if dec < 0 else ""
    dec = abs(dec)

    # Quantize to required decimal places
    quantize_format = "0." + "0" * decimal_places if decimal_places > 0 else "0"
    formatted_dec = f"{dec:.{decimal_places}f}"

    parts = formatted_dec.split(".")
    integer_part = parts[0]
    fractional_part = f".{parts[1]}" if len(parts) > 1 and decimal_places > 0 else ""

    # Indian numbering grouping: last 3 digits, then groups of 2 digits
    if len(integer_part) <= 3:
        grouped = integer_part
    else:
        last3 = integer_part[-3:]
        remaining = integer_part[:-3]
        # Group remaining in chunks of 2 from right to left
        groups = []
        while remaining:
            groups.append(remaining[-2:])
            remaining = remaining[:-2]
        groups.reverse()
        grouped = ",".join(groups) + "," + last3

    return f"₹ {sign}{grouped}{fractional_part}"
