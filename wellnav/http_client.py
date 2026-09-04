"""HTTP helpers for RRC sites.

webapps2.rrc.texas.gov uses an older TLS stack that Python's default
ssl/requests handshake often rejects. curl.exe negotiates it reliably,
so EWA traffic goes through curl with a cookie jar. GIS uses requests
first and falls back to curl.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import requests

BLOCKED_STATUS = {403, 408, 429, 500, 502, 503, 504}


class BlockedRequest(Exception):
    """Remote side refused, rate-limited, or dropped the request. Safe to retry later."""

    def __init__(self, message: str, retry_after: float | None = None, status: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

EWA_BASE = "https://webapps2.rrc.texas.gov"
GIS_BASE = "https://gis.rrc.texas.gov"


def _curl_bin() -> str:
    from shutil import which

    return which("curl.exe") or which("curl") or "curl"


class CurlSession:
    def __init__(self) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="rrc_cookies_", suffix=".txt", delete=False)
        handle.close()
        self.cookie_path = Path(handle.name)
        self._curl = _curl_bin()

    def request(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        timeout: int = 60,
    ) -> str:
        cmd = [
            self._curl,
            "-sL",
            "--max-time",
            str(timeout),
            "-A",
            USER_AGENT,
            "-b",
            str(self.cookie_path),
            "-c",
            str(self.cookie_path),
            "-H",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ]
        if method.upper() == "POST":
            cmd += ["-X", "POST"]
            if data is not None:
                cmd += [
                    "-H",
                    "Content-Type: application/x-www-form-urlencoded",
                    "--data",
                    urllib.parse.urlencode(data, doseq=True),
                ]
        cmd.append(url)
        try:
            completed = subprocess.run(cmd, capture_output=True, timeout=timeout + 5, check=False)
        except subprocess.TimeoutExpired as exc:
            raise BlockedRequest(f"curl timed out: {url}", retry_after=5) from exc
        if completed.returncode != 0:
            err = completed.stderr.decode("utf-8", errors="replace")
            raise BlockedRequest(f"curl failed ({completed.returncode}): {err or url}", retry_after=5)
        raw = completed.stdout
        for enc in ("utf-8", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace")

    def get(self, url: str, **kwargs) -> str:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, data: dict | None = None, **kwargs) -> str:
        return self.request("POST", url, data=data, **kwargs)


def _retry_after(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _looks_blocked_body(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return True
    return any(
        token in head
        for token in ("access denied", "rate limit", "too many requests", "forbidden", "captcha")
    )


def _parse_gis_json(text: str, source: str) -> dict:
    if _looks_blocked_body(text):
        raise BlockedRequest(f"{source} returned an HTML block page")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BlockedRequest(f"{source} returned non-JSON: {text[:180]!r}") from exc


def gis_get_json(url: str, params: dict | None = None, timeout: int = 45) -> dict:
    full = url
    if params:
        full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        resp = requests.get(
            full,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code in BLOCKED_STATUS:
            raise BlockedRequest(
                f"GIS HTTP {resp.status_code}",
                retry_after=_retry_after(resp) or 8,
                status=resp.status_code,
            )
        resp.raise_for_status()
        return _parse_gis_json(resp.text, "requests")
    except BlockedRequest:
        raise
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise BlockedRequest(f"GIS request failed: {exc}", retry_after=5) from exc
    except Exception:
        session = CurlSession()
        text = session.get(full, timeout=timeout)
        return _parse_gis_json(text, "curl")
