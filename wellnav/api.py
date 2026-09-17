"""API number normalize/display helpers. No network, no HTML parsing."""

from __future__ import annotations

import re

API_CLEAN = re.compile(r"\D+")


def normalize_api(value: str) -> tuple[str, str, str]:
    """Return (prefix, suffix, eight_digit) from user input."""
    digits = API_CLEAN.sub("", value or "")
    if digits.startswith("42") and len(digits) >= 10:
        digits = digits[2:10]
    elif len(digits) > 8:
        digits = digits[-8:]
    if len(digits) < 8:
        raise ValueError("Enter a full API number, e.g. 42-003-00290 or 00300290")
    eight = digits[:8]
    return eight[:3], eight[3:], eight


def format_api(eight: str) -> str:
    eight = API_CLEAN.sub("", eight)
    if len(eight) == 8:
        return f"42-{eight[:3]}-{eight[3:]}"
    return eight
