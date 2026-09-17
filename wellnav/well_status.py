"""Human labels for well symbols (PA, producing, injector, TA)."""

from __future__ import annotations

_PRODUCING = frozenset({"oil well", "gas well", "oil/gas well", "oil / gas well"})
_PRODUCING_TYPES = frozenset({"oil", "gas", "oil/gas", "oil and gas", "co2", "c02"})
_ACTIVE = frozenset({"active", "producing", "completed", "producing well"})


def _compact(value: str) -> str:
    return value.lower().replace(" ", "").replace("-", "")


def _is_injection(blob: str) -> bool:
    key = blob.lower()
    return any(token in key for token in ("inject", "disposal", "swd", "salt water"))


def _is_producing_type(well_type: str) -> bool:
    key = " ".join(well_type.lower().split())
    return key in _PRODUCING or key in _PRODUCING_TYPES or key.endswith(" well") and (
        "oil" in key or "gas" in key
    )


def status_label(
    symbol: str | None = None,
    *,
    record_kind: str = "as_drilled",
    permit_status: str | None = None,
    well_type: str | None = None,
) -> str:
    text = " ".join((symbol or "").split())
    kind = " ".join((well_type or "").split())
    if record_kind == "permit":
        code = (permit_status or "").strip().lower()
        if code == "cancelled":
            return "Canceled permit"
        if code == "expired":
            return "Expired permit"
        return text or "Permitted location"
    if not text and not kind:
        return ""
    blob = f"{text} {kind}".strip()
    compact = _compact(blob)
    detail = text or kind
    if "plugged" in compact:
        return f"PA · {detail}"
    if "temporar" in compact:
        return f"TA · {detail}"
    if "shutin" in compact:
        return f"Shut-in · {detail}"
    if _is_injection(blob):
        inj = kind if kind and _is_injection(kind) else detail
        return f"Injector · {inj}"
    if text.lower() in _PRODUCING:
        return f"Producing · {text}"
    if text.lower() in _ACTIVE and (_is_producing_type(kind) or not kind):
        return f"Producing · {kind or text}"
    if kind.lower() in _PRODUCING:
        return f"Producing · {kind}"
    if text.lower() in _ACTIVE and kind:
        return f"{text} · {kind}"
    return detail
