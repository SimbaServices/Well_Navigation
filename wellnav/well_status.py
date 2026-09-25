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


def status_match_sql(record_kind: str) -> str:
    """SQL expression that mirrors status_label() for column text filters."""
    if record_kind == "permit":
        return """
        CASE lower(trim(coalesce(status, '')))
          WHEN 'cancelled' THEN 'Canceled permit'
          WHEN 'expired' THEN 'Expired permit'
          ELSE CASE
            WHEN trim(coalesce(symbol, '')) != '' THEN trim(symbol)
            ELSE 'Permitted location'
          END
        END
        """
    blob = "lower(trim(coalesce(symbol, '')) || ' ' || trim(coalesce(well_type, '')))"
    compact = f"replace(replace({blob}, ' ', ''), '-', '')"
    detail = "coalesce(nullif(trim(symbol), ''), nullif(trim(well_type), ''), '')"
    injection = (
        f"instr({blob}, 'inject') > 0 OR instr({blob}, 'disposal') > 0 "
        f"OR instr({blob}, 'swd') > 0 OR instr({blob}, 'salt water') > 0"
    )
    producing = (
        "'oil well', 'gas well', 'oil/gas well', 'oil / gas well', "
        "'oil', 'gas', 'oil/gas', 'oil and gas', 'co2', 'c02'"
    )
    active = "'active', 'producing', 'completed', 'producing well'"
    return f"""
    CASE
      WHEN instr({compact}, 'plugged') > 0 THEN 'PA · ' || {detail}
      WHEN instr({compact}, 'temporar') > 0 THEN 'TA · ' || {detail}
      WHEN instr({compact}, 'shutin') > 0 THEN 'Shut-in · ' || {detail}
      WHEN {injection} THEN 'Injector · ' || CASE
        WHEN trim(coalesce(well_type, '')) != '' AND (
          instr(lower(well_type), 'inject') > 0
          OR instr(lower(well_type), 'disposal') > 0
          OR instr(lower(well_type), 'swd') > 0
          OR instr(lower(well_type), 'salt water') > 0
        ) THEN trim(well_type)
        ELSE {detail}
      END
      WHEN lower(trim(coalesce(symbol, ''))) IN ('oil well', 'gas well', 'oil/gas well', 'oil / gas well')
        THEN 'Producing · ' || trim(symbol)
      WHEN lower(trim(coalesce(symbol, ''))) IN ({active})
        AND (
          trim(coalesce(well_type, '')) = ''
          OR lower(trim(well_type)) IN ({producing})
          OR (
            lower(trim(well_type)) LIKE '% well'
            AND (instr(lower(well_type), 'oil') > 0 OR instr(lower(well_type), 'gas') > 0)
          )
        )
        THEN 'Producing · ' || coalesce(nullif(trim(well_type), ''), trim(symbol))
      WHEN lower(trim(coalesce(well_type, ''))) IN ('oil well', 'gas well', 'oil/gas well', 'oil / gas well')
        THEN 'Producing · ' || trim(well_type)
      WHEN lower(trim(coalesce(symbol, ''))) IN ({active})
        AND trim(coalesce(well_type, '')) != ''
        THEN trim(symbol) || ' · ' || trim(well_type)
      ELSE {detail}
    END
    """
