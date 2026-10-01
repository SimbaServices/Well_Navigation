"""Readable labels for the account history list."""

from __future__ import annotations

from datetime import datetime

_KIND_LABELS = {
    "search": "Search",
    "recent": "Recent",
    "location": "Location",
    "operators": "Operators",
}
_STATE_LABELS = {
    "tx": "Texas",
    "nm": "New Mexico",
    "ok": "Oklahoma",
    "la": "Louisiana",
    "all": "All states",
}
_MODE_LABELS = {
    "name": "Well name",
    "api": "API number",
    "operator": "Operator",
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def describe_cache_entries(entries: list[dict]) -> list[dict]:
    return [_describe(entry) for entry in entries]


def history_stats(stats: dict) -> dict:
    return {**stats, "size_label": _bytes_label(int(stats.get("bytes") or 0))}


def _describe(entry: dict) -> dict:
    kind = str(entry.get("kind") or "").strip().lower()
    key = str(entry.get("cache_key") or "").strip()
    parts = [part.strip() for part in key.split("|")]
    title, context = _copy(kind, parts, key)
    used, expires = _when_pair(entry.get("last_hit_at"), entry.get("expires_at"))
    bits = [_bytes_label(int(entry.get("bytes") or 0))]
    hits = int(entry.get("hits") or 0)
    if hits:
        bits.append("1 hit" if hits == 1 else f"{hits} hits")
    if used:
        bits.append(used)
    if expires:
        bits.append(f"expires {expires}")
    return {
        "id": entry.get("id"),
        "kind_label": _KIND_LABELS.get(kind, kind.replace("_", " ").title() or "Lookup"),
        "title": title,
        "context": context,
        "meta": " · ".join(bit for bit in bits if bit),
    }


def _copy(kind: str, parts: list[str], key: str) -> tuple[str, str]:
    if kind == "recent":
        return "Recent searches", ""
    if kind == "location":
        return _api_label(key) or "Well location", "Wellhead"
    if kind == "operators":
        query = parts[0] if parts else ""
        state = _state_label(parts[1] if len(parts) > 1 else "")
        return query or "Operator search", state
    if kind == "search":
        return _search_copy(parts)
    cleaned = " · ".join(part for part in parts if part) or key or "Saved lookup"
    return cleaned, ""


def _search_copy(parts: list[str]) -> tuple[str, str]:
    state = _state_label(parts[0] if parts else "")
    mode = _MODE_LABELS.get(parts[1] if len(parts) > 1 else "", "")
    name = parts[2] if len(parts) > 2 else ""
    api = parts[3] if len(parts) > 3 else ""
    numbers = parts[4] if len(parts) > 4 else ""
    names = parts[5] if len(parts) > 5 else ""
    lease = parts[6] if len(parts) > 6 else ""
    district = parts[7] if len(parts) > 7 else ""
    offset = parts[8] if len(parts) > 8 else ""
    filters = parts[10] if len(parts) > 10 else ""

    title = ""
    if name:
        title = name
    elif api:
        title = _api_label(api) or api
    elif names:
        title = names.replace(",", ", ")
    elif numbers:
        title = "Operator " + numbers.replace(",", ", ")
    elif lease:
        title = f"Lease {lease}"
        if district:
            title += f" · district {district}"
    else:
        title = mode or "Well search"

    context_bits = [bit for bit in (state, mode if title != mode else "") if bit]
    start = _result_start(offset)
    if start:
        context_bits.append(start)
    if filters:
        context_bits.append(filters.replace("=", " ").replace(",", " · "))
    return title, " · ".join(context_bits)


def _result_start(offset: str) -> str:
    if not offset or offset in {"0", "0.0"}:
        return ""
    try:
        start = int(float(offset))
    except ValueError:
        return ""
    if start <= 0:
        return ""
    return f"from result {start + 1}"


def _state_label(code: str) -> str:
    key = (code or "").strip().lower()
    if not key:
        return ""
    return _STATE_LABELS.get(key, key.upper())


def _api_label(raw: str) -> str:
    digits = "".join(ch for ch in raw or "" if ch.isdigit())
    if len(digits) >= 10:
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}"
    if len(digits) == 8:
        return f"{digits[:3]}-{digits[3:]}"
    return (raw or "").strip()


def _bytes_label(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        text = f"{size / 1024:.1f}".rstrip("0").rstrip(".")
        return f"{text} KB"
    text = f"{size / (1024 * 1024):.1f}".rstrip("0").rstrip(".")
    return f"{text} MB"


def _parse_time(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _day_time(when: datetime) -> str:
    return f"{_MONTHS[when.month - 1]} {when.day}, {when.hour:02d}:{when.minute:02d}"


def _clock(when: datetime) -> str:
    return f"{when.hour:02d}:{when.minute:02d}"


def _when_pair(used_raw: object, expires_raw: object) -> tuple[str, str]:
    used = _parse_time(used_raw)
    expires = _parse_time(expires_raw)
    if used and expires and used.date() == expires.date():
        return _day_time(used), _clock(expires)
    return (_day_time(used) if used else "", _day_time(expires) if expires else "")
