"""Simba Services UX session recordings. Stored on the app host, not in SQLite."""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wellnav.billing import complimentary_email
from wellnav.db import ROOT

RECORDING_ID_RE = re.compile(r"^[a-f0-9]{16,32}$")
MAX_RECORDING_BYTES = 250 * 1024 * 1024
SNAPSHOT_PROBE_BYTES = 1024 * 1024
HOTJAR_ID_RE = re.compile(r"^\d{5,12}$")
CLARITY_ID_RE = re.compile(r"^[a-f0-9]{8,20}$")
CONTENTSQUARE_ID_RE = re.compile(r"^[a-f0-9]{10,16}$")


def recordings_dir() -> Path:
    raw = (os.environ.get("WELLNAV_RECORDINGS_DIR") or "").strip()
    path = Path(raw) if raw else ROOT / "ux-recordings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_watch_dir() -> Path:
    raw = (os.environ.get("WELLNAV_RECORDINGS_DIR") or "").strip()
    if raw:
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        return path
    pulled = ROOT / "data" / "ux-recordings"
    host = ROOT / "ux-recordings"
    pulled_n = len(list(pulled.glob("*.json"))) if pulled.is_dir() else 0
    host_n = len(list(host.glob("*.json"))) if host.is_dir() else 0
    path = pulled if pulled_n >= host_n else host
    path.mkdir(parents=True, exist_ok=True)
    return path


def can_record(user: dict | None) -> bool:
    if not user:
        return False
    return complimentary_email(user.get("email") or user.get("username") or "")


def _env_file_value(name: str) -> str:
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    prefix = f"{name}="
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        text = line.strip()
        if text.startswith("#") or not text.startswith(prefix):
            continue
        return text.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _raw_analytics_id(*names: str) -> str:
    for name in names:
        raw = (os.environ.get(name) or "").strip() or _env_file_value(name)
        if raw:
            return raw
    return ""


def hotjar_site_id() -> str:
    raw = _raw_analytics_id("WELLNAV_HOTJAR_ID", "HOTJAR_ID")
    return raw if HOTJAR_ID_RE.match(raw) else ""


def clarity_project_id() -> str:
    raw = _raw_analytics_id("WELLNAV_CLARITY_ID", "CLARITY_ID", "WELLNAV_HOTJAR_ID", "HOTJAR_ID")
    text = raw.lower()
    return text if CLARITY_ID_RE.match(text) else ""


def contentsquare_tag_id() -> str:
    raw = _raw_analytics_id("WELLNAV_CONTENTSQUARE_ID", "CONTENTSQUARE_ID")
    text = raw.lower()
    return text if CONTENTSQUARE_ID_RE.match(text) else ""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_id(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if not RECORDING_ID_RE.match(text):
        return None
    return text


def _meta_path(recording_id: str) -> Path:
    return recordings_dir() / f"{recording_id}.json"


def _data_path(recording_id: str) -> Path:
    folder = recordings_dir()
    jsonl = folder / f"{recording_id}.jsonl"
    webm = folder / f"{recording_id}.webm"
    if webm.is_file() and not jsonl.is_file():
        return webm
    return jsonl


def _read_meta(recording_id: str) -> dict[str, Any] | None:
    meta_path = _meta_path(recording_id)
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_meta(recording_id: str, meta: dict[str, Any]) -> None:
    _meta_path(recording_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file() or path.suffix == ".webm":
        return events
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return events
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        batch = parsed if isinstance(parsed, list) else [parsed]
        for item in batch:
            if isinstance(item, dict) and item.get("type") is not None:
                events.append(item)
    return events


def session_playable(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    if path.suffix == ".webm":
        return True
    try:
        sample = path.read_bytes()[:SNAPSHOT_PROBE_BYTES]
    except OSError:
        return False
    return b'"type":2,"data":{"node"' in sample or b'"type":2, "data": {"node"' in sample


def start_recording(user: dict, user_agent: str = "") -> dict[str, Any]:
    if not can_record(user):
        raise PermissionError("Screen recording is only available to Simba Services.")
    recording_id = secrets.token_hex(16)
    _data_path(recording_id).write_bytes(b"")
    meta = {
        "id": recording_id,
        "user_id": int(user["id"]),
        "email": (user.get("email") or user.get("username") or "").strip().lower(),
        "started_at": _utc_stamp(),
        "finished_at": "",
        "bytes": 0,
        "kind": "session",
        "user_agent": (user_agent or "")[:300],
    }
    _write_meta(recording_id, meta)
    return meta


def append_chunk(recording_id: str, user: dict, payload: bytes) -> dict[str, Any]:
    if not can_record(user):
        raise PermissionError("Screen recording is only available to Simba Services.")
    safe = _safe_id(recording_id)
    if not safe:
        raise ValueError("That recording was not found.")
    meta = _read_meta(safe)
    if not meta or int(meta.get("user_id") or 0) != int(user["id"]):
        raise ValueError("That recording was not found.")
    data = payload or b""
    if not data:
        return meta
    if not data.endswith(b"\n"):
        data = data + b"\n"
    video_path = _data_path(safe)
    current = video_path.stat().st_size if video_path.is_file() else 0
    if current + len(data) > MAX_RECORDING_BYTES:
        raise ValueError("That recording is too large to save.")
    with video_path.open("ab") as handle:
        handle.write(data)
    meta["bytes"] = current + len(data)
    meta["finished_at"] = ""
    _write_meta(safe, meta)
    return meta


def finish_recording(recording_id: str, user: dict) -> dict[str, Any]:
    if not can_record(user):
        raise PermissionError("Screen recording is only available to Simba Services.")
    safe = _safe_id(recording_id)
    if not safe:
        raise ValueError("That recording was not found.")
    meta = _read_meta(safe)
    if not meta or int(meta.get("user_id") or 0) != int(user["id"]):
        raise ValueError("That recording was not found.")
    video_path = _data_path(safe)
    meta["bytes"] = video_path.stat().st_size if video_path.is_file() else 0
    meta["finished_at"] = _utc_stamp()
    _write_meta(safe, meta)
    return meta


def list_recordings() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for meta_path in recordings_dir().glob("*.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        video_path = _data_path(str(data["id"]))
        data["bytes"] = video_path.stat().st_size if video_path.is_file() else int(data.get("bytes") or 0)
        data["playable"] = session_playable(video_path)
        data["media"] = "video" if video_path.suffix == ".webm" else "session"
        rows.append(data)
    rows.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return rows


def recording_file(recording_id: str) -> tuple[Path, dict[str, Any]] | None:
    safe = _safe_id(recording_id)
    if not safe:
        return None
    meta = _read_meta(safe)
    if not meta:
        return None
    video_path = _data_path(safe)
    if not video_path.is_file():
        return None
    meta["playable"] = session_playable(video_path)
    meta["media"] = "video" if video_path.suffix == ".webm" else "session"
    return video_path, meta
