"""Stacked search-filter state from query/form fields."""

from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlencode

from wellnav.operators import normalize_operator_name, operator_identity, standardize_operator_name


def _split_opn(raw: str) -> tuple[str, str] | None:
    text = (raw or "").strip()
    if not text or "|" not in text:
        return None
    number, name = text.split("|", 1)
    number, name = number.strip(), name.strip()
    if not number and not name:
        return None
    return number, name


def _list(source, key: str) -> list[str]:
    if hasattr(source, "getlist"):
        values = source.getlist(key)
    elif isinstance(source, dict):
        raw = source.get(key, [])
        if isinstance(raw, (list, tuple)):
            values = list(raw)
        elif raw:
            values = [raw]
        else:
            values = []
    else:
        values = []
    return [str(value).strip() for value in values if str(value).strip()]


def _one(source, key: str) -> str:
    if hasattr(source, "get"):
        value = source.get(key)
    else:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _collapse_operator_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str, set[str]]]:
    """Merge punctuation variants of one company; keep distinct P-5 numbers split."""
    groups: dict[str, tuple[str, str, set[str]]] = {}
    order: list[str] = []
    extras = 0
    for number, name in pairs:
        display = standardize_operator_name(name) or name or number
        key = operator_identity(number, name)
        if not key:
            continue
        if key in groups:
            prev_number, prev_name, aliases = groups[key]
            prev_std = standardize_operator_name(prev_name)
            if (
                prev_number
                and number
                and prev_number != number
                and prev_std != display
            ):
                extras += 1
                key = f"num:{number}:{extras}"
                groups[key] = (number, display, {number, key})
                order.append(key)
                continue
            if number:
                aliases.add(number)
            aliases.add(key)
            if (not prev_number or prev_number == key) and number:
                groups[key] = (number, prev_name or display, aliases)
            continue
        groups[key] = (number or key, display, {number, key} if number else {key})
        order.append(key)
    return [groups[key] for key in order]


def parse_filters(source) -> dict:
    """Read stacked filters from a Starlette QueryParams, FormData, or dict."""
    raw_ops: list[tuple[str, str]] = []
    for raw in _list(source, "opn"):
        parsed = _split_opn(raw)
        if parsed:
            raw_ops.append(parsed)

    add_number = _one(source, "add_op_number")
    add_name = _one(source, "add_op_name")
    if add_number or add_name:
        raw_ops.append((add_number, add_name))

    legacy = _one(source, "operator_number")
    if legacy:
        raw_ops.append((legacy, _one(source, "operator_name")))

    if _one(source, "clear_filters") == "1":
        return empty_filters()

    remove_op = _one(source, "remove_op")
    collapsed = _collapse_operator_pairs(raw_ops)
    if remove_op:
        collapsed = [
            item
            for item in collapsed
            if remove_op not in item[2] and remove_op != item[0] and remove_op != item[1]
        ]

    if hasattr(source, "multi_items"):
        submitted_keys = {key for key, _ in source.multi_items()}
    elif hasattr(source, "keys"):
        submitted_keys = set(source.keys())
    else:
        submitted_keys = set(source)

    checked = set(_list(source, "op"))
    if add_number:
        checked.add(add_number)
    if add_name and not add_number:
        checked.add(normalize_operator_name(add_name))
    if remove_op:
        checked.discard(remove_op)
    if "opn" not in submitted_keys:
        if not checked:
            checked = {item[0] for item in collapsed} | {item[1] for item in collapsed}
        elif legacy:
            checked.add(legacy)

    operators = []
    for number, name, aliases in collapsed:
        operators.append(
            {
                "number": number,
                "name": name,
                "active": bool(checked & (aliases | {number, name})),
            }
        )

    name = _one(source, "name")
    api = _one(source, "api")
    if _one(source, "remove_name") == "1":
        name = ""
    if _one(source, "remove_api") == "1":
        api = ""

    name_active = bool(name) and _one(source, "use_name") == "1" and _one(source, "remove_name") != "1"
    api_active = bool(api) and _one(source, "use_api") == "1" and _one(source, "remove_api") != "1"

    return {
        "operators": operators,
        "name": name,
        "name_active": name_active,
        "api": api,
        "api_active": api_active,
    }


COLUMN_FILTER_KEYS = ("name", "api", "status", "operator", "lease", "county")


def parse_column_filters(source) -> dict[str, str]:
    """Per-column text filters from cf_* query fields. Blank values are omitted."""
    found: dict[str, str] = {}
    for key in COLUMN_FILTER_KEYS:
        value = _one(source, f"cf_{key}")
        if value:
            found[key] = value[:80]
    return found


def empty_filters() -> dict:
    return {
        "operators": [],
        "name": "",
        "name_active": False,
        "api": "",
        "api_active": False,
    }


def apply_search_input(filters: dict, *, mode: str, q: str) -> dict:
    """Turn the current search-box submit into a stacked filter."""
    q = (q or "").strip()
    if not q:
        return filters
    if mode == "name":
        filters["name"] = q
        filters["name_active"] = True
    elif mode == "api":
        filters["api"] = q
        filters["api_active"] = True
    return filters


# A lone well-name or API keystroke is too broad to scan the full tables.
# One character is enough once another filter already limits the set.
LIVE_FILTER_MIN = 2


def typed_query_filters(
    filters: dict,
    *,
    mode: str,
    q: str,
    committing: bool,
    live: bool = False,
) -> dict:
    """Apply a well name or API from the search box.

    Submitting stores it on a chip. A live request replaces that chip with
    whatever is currently typed, so clearing the box clears the filter.
    """
    text = (q or "").strip()
    if live and mode in {"name", "api"}:
        filters = deepcopy(filters)
        if mode == "name":
            filters["name"] = ""
            filters["name_active"] = False
        else:
            filters["api"] = ""
            filters["api_active"] = False
        minimum = 1 if has_filters(filters) else LIVE_FILTER_MIN
        if len(text) >= minimum:
            return apply_search_input(filters, mode=mode, q=text)
        return filters
    if committing and mode in {"name", "api"} and text:
        return apply_search_input(filters, mode=mode, q=text)
    return filters


def has_filters(filters: dict) -> bool:
    return bool(
        active_operator_numbers(filters)
        or active_operator_names(filters)
        or (filters.get("name") and filters.get("name_active"))
        or (filters.get("api") and filters.get("api_active"))
    )


def has_filter_chips(filters: dict) -> bool:
    return bool(filters.get("operators") or filters.get("name") or filters.get("api"))


def active_operator_numbers(filters: dict) -> list[str]:
    return [op["number"] for op in filters.get("operators") or [] if op.get("active") and op.get("number")]


def active_operator_names(filters: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for op in filters.get("operators") or []:
        if not op.get("active"):
            continue
        name = normalize_operator_name(op.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def search_kwargs(filters: dict) -> dict:
    return {
        "operator_numbers": active_operator_numbers(filters),
        "operator_names": active_operator_names(filters),
        "name": filters["name"] if filters.get("name_active") else "",
        "api": filters["api"] if filters.get("api_active") else "",
    }


def filter_query(filters: dict, **extra: object) -> str:
    parts: list[tuple[str, str]] = []
    for op in filters.get("operators") or []:
        parts.append(("opn", f"{op['number']}|{op['name']}"))
        if op.get("active"):
            parts.append(("op", op["number"]))
    if filters.get("name"):
        parts.append(("name", filters["name"]))
        if filters.get("name_active"):
            parts.append(("use_name", "1"))
    if filters.get("api"):
        parts.append(("api", filters["api"]))
        if filters.get("api_active"):
            parts.append(("use_api", "1"))
    for key, value in extra.items():
        if value not in (None, ""):
            parts.append((key, str(value)))
    return urlencode(parts)


def subtitle(filters: dict, *, mode: str = "", q: str = "") -> str:
    bits = []
    active_ops = [op["name"] for op in filters.get("operators") or [] if op.get("active")]
    if active_ops:
        if len(active_ops) == 1:
            bits.append(f"Operator {active_ops[0]}")
        else:
            bits.append(f"{len(active_ops)} operators")
    if filters.get("name_active") and filters.get("name"):
        bits.append(f"Name “{filters['name']}”")
    if filters.get("api_active") and filters.get("api"):
        bits.append(f"API {filters['api']}")
    if not bits and q:
        if mode == "api":
            return f"API {q}"
        if mode == "operator":
            return f"Operator {q}"
        return f"Name “{q}”"
    return " · ".join(bits)
