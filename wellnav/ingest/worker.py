"""One county partition: pull GIS well + surface layers, classify, return rows."""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from wellnav.gis import LAYER_SURFACE, LAYER_WELL_LOCATIONS, fetch_layer
from wellnav.http_client import BlockedRequest
from wellnav.ingest.classify import merge_county_features
from wellnav.ingest.identity import fetch_county_identity, merge_identity
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
        "wells": [],
        "permits": [],
        "error": error,
        "retry_after": retry_after,
        "attempt": int(payload.get("attempt") or 1),
        "default_features": 0,
        "surface_features": 0,
        "identity_count": 0,
        "identities": [],
        "identity_only": bool(payload.get("identity_only")),
    }


def run_partition(payload: dict) -> dict:
    county_code = payload["county_code"]
    county_name = payload["county_name"]
    lifetime_days = int(payload.get("lifetime_days") or DEFAULT_PERMIT_LIFETIME_DAYS)
    delay = float(payload.get("delay") or 0.12)
    permit_only = bool(payload.get("permit_only"))
    identity_only = bool(payload.get("identity_only"))
    try:
        if identity_only:
            return _run_identity(payload, county_code, county_name, delay)

        where = f"API LIKE '{county_code}%'"
        if permit_only:
            nums = ",".join(str(n) for n in sorted(PERMIT_SYMNUMS))
            default_where = f"({where}) AND SYMNUM IN ({nums})"
        else:
            default_where = where

        scratch = _load_scratch(payload)
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
                    },
                )
                return _blocked(
                    payload,
                    county_code,
                    county_name,
                    page.get("error") or "GIS surface layer blocked",
                    page.get("retry_after"),
                )
            scratch["surfaces_complete"] = True
            scratch["surface_offset"] = page["offset"]

        scratch["defaults"] = defaults
        scratch["surfaces"] = surfaces
        scratch["defaults_complete"] = True
        if not permit_only:
            scratch["surfaces_complete"] = True

        wells, permits = merge_county_features(
            defaults,
            surfaces,
            county_code=county_code,
            county_name=county_name,
            lifetime_days=lifetime_days,
        )
        if permit_only:
            wells = []
        identities = _pull_identity(payload, scratch, county_code, delay)
        merge_identity(wells, identities)
        merge_identity(permits, identities)
        _clear_scratch(payload)
        return {
            "county_code": county_code,
            "county_name": county_name,
            "ok": True,
            "blocked": False,
            "wells": wells,
            "permits": permits,
            "identities": identities,
            "identity_count": len(identities),
            "identity_only": False,
            "error": None,
            "attempt": int(payload.get("attempt") or 1),
            "default_features": len(defaults),
            "surface_features": len(surfaces),
        }
    except BlockedRequest as exc:
        return _blocked(payload, county_code, county_name, str(exc), exc.retry_after)
    except Exception as exc:
        return {
            "county_code": county_code,
            "county_name": county_name,
            "ok": False,
            "blocked": False,
            "wells": [],
            "permits": [],
            "identities": [],
            "identity_count": 0,
            "identity_only": identity_only,
            "error": f"{exc}\n{traceback.format_exc()}",
            "attempt": int(payload.get("attempt") or 1),
            "default_features": 0,
            "surface_features": 0,
        }


def _identity_scratch(scratch: dict) -> dict:
    return {
        "identity_wells": list(scratch.get("identity_wells") or []),
        "identity_queue": scratch.get("identity_queue"),
        "identity_seen_specs": list(scratch.get("identity_seen_specs") or []),
        "identity_complete": bool(scratch.get("identity_complete")),
    }


def _pull_identity(payload: dict, scratch: dict, county_code: str, delay: float) -> list[dict]:
    ident_state = _identity_scratch(scratch)
    try:
        result = fetch_county_identity(county_code, delay=delay, scratch=ident_state)
    except BlockedRequest as exc:
        merged = dict(scratch)
        merged.update(ident_state)
        _save_scratch(payload, merged)
        raise exc
    return list(result.get("wells") or [])


def _run_identity(payload: dict, county_code: str, county_name: str, delay: float) -> dict:
    scratch = _load_scratch(payload)
    identities = _pull_identity(payload, scratch, county_code, delay)
    _clear_scratch(payload)
    return {
        "county_code": county_code,
        "county_name": county_name,
        "ok": True,
        "blocked": False,
        "wells": [],
        "permits": [],
        "identities": identities,
        "identity_count": len(identities),
        "identity_only": True,
        "error": None,
        "attempt": int(payload.get("attempt") or 1),
        "default_features": 0,
        "surface_features": 0,
    }
