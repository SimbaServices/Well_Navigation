"""One county partition: pull GIS well + surface layers, classify, return rows."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from wellnav.gis import LAYER_SURFACE, LAYER_WELL_LOCATIONS, fetch_layer
from wellnav.http_client import BlockedRequest
from wellnav.ingest.classify import merge_county_features
from wellnav.ingest.identity import fetch_county_identities
from wellnav.ingest.persist import IDENTITY_FIELDS
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, PERMIT_SYMNUMS


def _scratch_path(payload: dict) -> Path | None:
    raw = payload.get("scratch_path")
    return Path(raw) if raw else None


def _load_scratch(payload: dict) -> dict:
    path = _scratch_path(payload)
    if not path or not path.exists():
        return {
            "defaults": [],
            "surfaces": [],
            "default_offset": 0,
            "surface_offset": 0,
            "defaults_complete": False,
            "surfaces_complete": False,
            "identities": [],
            "identity_queue": [],
            "identity_complete": False,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "defaults": [],
            "surfaces": [],
            "default_offset": 0,
            "surface_offset": 0,
            "defaults_complete": False,
            "surfaces_complete": False,
            "identities": [],
            "identity_queue": [],
            "identity_complete": False,
        }


def _save_scratch(payload: dict, state: dict) -> None:
    path = _scratch_path(payload)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _clear_scratch(payload: dict) -> None:
    path = _scratch_path(payload)
    if path and path.exists():
        path.unlink()


def _blocked(payload: dict, county_code: str, county_name: str, error: str, retry_after: float | None) -> dict:
    return {
        "county_code": county_code,
        "county_name": county_name,
        "ok": False,
        "blocked": True,
        "identity_only": bool(payload.get("identity_only")),
        "wells": [],
        "permits": [],
        "identities": [],
        "error": error,
        "retry_after": retry_after,
        "attempt": int(payload.get("attempt") or 1),
        "default_features": 0,
        "surface_features": 0,
        "identity_features": 0,
    }


def _attach_identity(records: list[dict], identities: list[dict]) -> None:
    by_api = {}
    for row in identities:
        api8 = (row.get("api8") or row.get("api") or "").strip()
        digits = "".join(ch for ch in api8 if ch.isdigit())
        if digits.startswith("42") and len(digits) >= 10:
            digits = digits[2:]
        api8 = digits[:8]
        if len(api8) == 8:
            by_api[api8] = row
    for record in records:
        ident = by_api.get(record.get("api8") or "")
        if not ident:
            continue
        for name in IDENTITY_FIELDS:
            incoming = (ident.get(name) or "").strip()
            if incoming:
                record[name] = incoming


def _run_identity(payload: dict, scratch: dict, county_code: str, county_name: str) -> dict:
    result = fetch_county_identities(
        county_code,
        scratch=scratch,
        delay=float(payload.get("delay") or 0.12),
    )
    identities = result.get("identities") or []
    if result.get("blocked"):
        merged = dict(scratch)
        merged.update(result.get("scratch") or {})
        _save_scratch(payload, merged)
        return _blocked(
            payload,
            county_code,
            county_name,
            result.get("error") or "EWA identity query blocked",
            result.get("retry_after"),
        )
    scratch.update(result.get("scratch") or {})
    scratch["identity_complete"] = True
    scratch["identities"] = identities
    return {
        "ok": True,
        "identities": identities,
        "identity_features": len(identities),
    }


def run_partition(payload: dict) -> dict:
    county_code = payload["county_code"]
    county_name = payload["county_name"]
    lifetime_days = int(payload.get("lifetime_days") or DEFAULT_PERMIT_LIFETIME_DAYS)
    delay = float(payload.get("delay") or 0.12)
    permit_only = bool(payload.get("permit_only"))
    identity_only = bool(payload.get("identity_only"))
    try:
        scratch = _load_scratch(payload)
        if identity_only:
            fetched = _run_identity(payload, scratch, county_code, county_name)
            if not fetched.get("ok"):
                return fetched
            identities = fetched.get("identities") or []
            _clear_scratch(payload)
            return {
                "county_code": county_code,
                "county_name": county_name,
                "ok": True,
                "blocked": False,
                "identity_only": True,
                "identities": identities,
                "wells": identities,
                "permits": identities,
                "error": None,
                "attempt": int(payload.get("attempt") or 1),
                "default_features": 0,
                "surface_features": 0,
                "identity_features": len(identities),
            }

        where = f"API LIKE '{county_code}%'"
        if permit_only:
            nums = ",".join(str(n) for n in sorted(PERMIT_SYMNUMS))
            default_where = f"({where}) AND SYMNUM IN ({nums})"
        else:
            default_where = where

        defaults = list(scratch.get("defaults") or [])
        surfaces = list(scratch.get("surfaces") or [])

        if not scratch.get("defaults_complete"):
            page = fetch_layer(
                LAYER_WELL_LOCATIONS,
                default_where,
                start_offset=int(scratch.get("default_offset") or 0),
                delay=delay,
            )
            defaults.extend(page["features"])
            if page.get("blocked"):
                _save_scratch(
                    payload,
                    {
                        "defaults": defaults,
                        "surfaces": surfaces,
                        "default_offset": page["offset"],
                        "surface_offset": scratch.get("surface_offset") or 0,
                        "defaults_complete": False,
                        "surfaces_complete": False,
                        "identities": scratch.get("identities") or [],
                        "identity_queue": scratch.get("identity_queue") or [],
                        "identity_complete": False,
                    },
                )
                return _blocked(
                    payload,
                    county_code,
                    county_name,
                    page.get("error") or "GIS well layer blocked",
                    page.get("retry_after"),
                )
            scratch["defaults_complete"] = True
            scratch["default_offset"] = page["offset"]

        if not permit_only and not scratch.get("surfaces_complete"):
            page = fetch_layer(
                LAYER_SURFACE,
                where,
                start_offset=int(scratch.get("surface_offset") or 0),
                delay=delay,
            )
            surfaces.extend(page["features"])
            if page.get("blocked"):
                _save_scratch(
                    payload,
                    {
                        "defaults": defaults,
                        "surfaces": surfaces,
                        "default_offset": scratch.get("default_offset") or 0,
                        "surface_offset": page["offset"],
                        "defaults_complete": True,
                        "surfaces_complete": False,
                        "identities": scratch.get("identities") or [],
                        "identity_queue": scratch.get("identity_queue") or [],
                        "identity_complete": False,
                    },
                )
                return _blocked(
                    payload,
                    county_code,
                    county_name,
                    page.get("error") or "GIS surface layer blocked",
                    page.get("retry_after"),
                )

        wells, permits = merge_county_features(
            defaults,
            surfaces,
            county_code=county_code,
            county_name=county_name,
            lifetime_days=lifetime_days,
        )
        if permit_only:
            wells = []
        if not scratch.get("identity_complete"):
            fetched = _run_identity(payload, scratch, county_code, county_name)
            if not fetched.get("ok"):
                scratch["defaults"] = defaults
                scratch["surfaces"] = surfaces
                scratch["defaults_complete"] = True
                scratch["surfaces_complete"] = True
                _save_scratch(payload, scratch)
                return fetched
            _attach_identity(wells, fetched.get("identities") or [])
            _attach_identity(permits, fetched.get("identities") or [])
            identity_features = fetched.get("identity_features") or 0
        else:
            identity_features = len(scratch.get("identities") or [])
            _attach_identity(wells, scratch.get("identities") or [])
            _attach_identity(permits, scratch.get("identities") or [])
        _clear_scratch(payload)
        return {
            "county_code": county_code,
            "county_name": county_name,
            "ok": True,
            "blocked": False,
            "identity_only": False,
            "identities": scratch.get("identities") or [],
            "wells": wells,
            "permits": permits,
            "error": None,
            "attempt": int(payload.get("attempt") or 1),
            "default_features": len(defaults),
            "surface_features": len(surfaces),
            "identity_features": identity_features,
        }
    except BlockedRequest as exc:
        return _blocked(payload, county_code, county_name, str(exc), exc.retry_after)
    except Exception as exc:
        return {
            "county_code": county_code,
            "county_name": county_name,
            "ok": False,
            "blocked": False,
            "identity_only": identity_only,
            "wells": [],
            "permits": [],
            "identities": [],
            "error": f"{exc}\n{traceback.format_exc()}",
            "attempt": int(payload.get("attempt") or 1),
            "default_features": 0,
            "surface_features": 0,
            "identity_features": 0,
        }
