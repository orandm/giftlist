"""Money as integer minor units (cents). Never floats."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}

# A ceiling against fat-fingered extra zeros (and giant strings thrown at the parser),
# not a real-world price limit -- comfortably above anything a wishlist item should cost.
MAX_MINOR = 100_000_000  # 1,000,000.00


class InvalidAmount(ValueError):
    pass


def parse_amount(text: str) -> int:
    """'80' -> 8000, '79.9' -> 7990, '1,250.50' -> 125050. Must be > 0."""
    text = text.strip()
    if len(text) > 20:
        raise InvalidAmount("That's not an amount. Use a number like 25 or 19.99.")
    cleaned = text.replace(",", "").lstrip("€£$").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise InvalidAmount(f"'{text}' isn't an amount. Use a number like 25 or 19.99.") from None
    if not value.is_finite() or value.as_tuple().exponent < -2:
        raise InvalidAmount("Use at most two decimal places.")
    if value <= 0:
        raise InvalidAmount("Amount must be more than zero.")
    minor = int(value * 100)
    if minor > MAX_MINOR:
        raise InvalidAmount(f"Keep it under {MAX_MINOR // 100:,}. Split it into a few items if it's really that much.")
    return minor


def format_amount(minor: int, currency: str) -> str:
    symbol = _SYMBOLS.get(currency, f"{currency} ")
    whole, cents = divmod(minor, 100)
    return f"{symbol}{whole:,}" if cents == 0 else f"{symbol}{whole:,}.{cents:02d}"


def to_input(minor: int) -> str:
    """Value for pre-filling a form field."""
    whole, cents = divmod(minor, 100)
    return str(whole) if cents == 0 else f"{whole}.{cents:02d}"
