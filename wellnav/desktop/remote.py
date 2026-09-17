"""Talk to SQLite files inside the remote wellnav container over SSH."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from wellnav.desktop.helper import format_size

HELPER_NAME = "wellnav_desktop_helper.py"
HELPER_SRC = Path(__file__).with_name("helper.py")
REMOTE_HELPER_HOST = f"/tmp/{HELPER_NAME}"
REMOTE_HELPER_CONTAINER = f"/tmp/{HELPER_NAME}"
DEFAULT_HOST = "wellnav"
DEFAULT_CONTAINER = "wellnav"


class RemoteError(RuntimeError):
    pass


class RemoteStore:
    def __init__(self, host: str = DEFAULT_HOST, container: str = DEFAULT_CONTAINER) -> None:
        self.host = host
        self.container = container
        self.connected = False

    def close(self) -> None:
        self.connected = False

    def _ssh(self, remote_cmd: str, *, timeout: int = 45, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", self.host, remote_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=check,
        )

    def connect(self) -> dict:
        if not HELPER_SRC.exists():
            raise RemoteError(f"missing helper {HELPER_SRC}")
        scp = subprocess.run(
            ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", str(HELPER_SRC), f"{self.host}:{REMOTE_HELPER_HOST}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        if scp.returncode != 0:
            raise RemoteError(scp.stderr.strip() or "scp helper failed")
        copied = self._ssh(f"docker cp {REMOTE_HELPER_HOST} {self.container}:{REMOTE_HELPER_CONTAINER}")
        if copied.returncode != 0:
            raise RemoteError(copied.stderr.strip() or "docker cp helper failed")
        ping = self.request({"op": "ping"}, timeout=30)
        self.connected = True
        return ping

    def request(self, payload: dict, *, timeout: int = 180) -> object:
        proc = subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                self.host,
                f"docker exec -i {self.container} python -u {REMOTE_HELPER_CONTAINER}",
            ],
            input=json.dumps(payload, ensure_ascii=False) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            raise RemoteError(proc.stderr.strip() or f"ssh failed ({proc.returncode})")
        body = _first_json(proc.stdout)
        if not body.get("ok"):
            raise RemoteError(body.get("error") or proc.stderr.strip() or "remote helper failed")
        return body.get("data")

    def list_databases(self, extra=None) -> list[dict]:
        rows = self.request({"op": "list_databases"})
        return rows or []

    def file_info(self, path: str) -> dict:
        return self.request({"op": "file_info", "path": str(path)})

    def list_tables(self, path: str, *, hide_empty: bool = True, hide_internal: bool = True) -> list[dict]:
        return self.request({"op": "list_tables", "path": str(path), "hide_empty": hide_empty, "hide_internal": hide_internal})

    def browse(self, path: str, table: str, **kwargs) -> dict:
        payload = {"op": "browse", "path": str(path), "table": table}
        payload.update(kwargs)
        return self.request(payload)

    def execute_sql(self, path: str, sql: str, *, max_rows: int = 500) -> dict:
        return self.request({"op": "execute_sql", "path": str(path), "sql": sql, "max_rows": max_rows})

    def delete_rows(self, path: str, table: str, rows: list[dict]) -> int:
        return int(self.request({"op": "delete_rows", "path": str(path), "table": table, "rows": rows}))

    def integrity(self, path: str) -> str:
        return str(self.request({"op": "integrity", "path": str(path)}, timeout=300))

    def checkpoint(self, path: str) -> str:
        return str(self.request({"op": "checkpoint", "path": str(path)}))

    def vacuum(self, path: str) -> None:
        self.request({"op": "vacuum", "path": str(path)}, timeout=600)

    def export_csv(self, path: str, table: str, dest: Path, *, search: str = "") -> int:
        remote_csv = f"/tmp/{Path(table).name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        count = int(
            self.request(
                {"op": "export_csv", "path": str(path), "table": table, "dest": remote_csv, "search": search},
                timeout=600,
            )
        )
        self._pull_file(f"{self.container}:{remote_csv}", dest)
        return count

    def backup(self, path: str, dest: Path) -> None:
        remote_db = f"/tmp/{Path(str(path)).stem}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        self.request({"op": "backup", "path": str(path), "dest": remote_db}, timeout=600)
        self._pull_file(f"{self.container}:{remote_db}", dest)

    def replace_with(self, path: str, source: Path) -> dict:
        remote_src = f"/tmp/{Path(source).name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        self._push_file(source, remote_src)
        result = self.request({"op": "replace_with", "path": str(path), "source": remote_src}, timeout=180)
        restarted = self._ssh(f"docker restart {self.container}", timeout=90)
        if restarted.returncode != 0:
            raise RemoteError(restarted.stderr.strip() or "docker restart failed")
        return result

    def _pull_file(self, container_spec: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        host_tmp = f"/tmp/{dest.name}"
        copied = self._ssh(f"docker cp {container_spec} {host_tmp}", timeout=300)
        if copied.returncode != 0:
            raise RemoteError(copied.stderr.strip() or "docker cp download failed")
        scp = subprocess.run(
            ["scp", "-q", "-o", "BatchMode=yes", f"{self.host}:{host_tmp}", str(dest)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if scp.returncode != 0:
            raise RemoteError(scp.stderr.strip() or "scp download failed")

    def _push_file(self, source: Path, container_dest: str) -> None:
        host_tmp = f"/tmp/{Path(container_dest).name}"
        scp = subprocess.run(
            ["scp", "-q", "-o", "BatchMode=yes", str(source), f"{self.host}:{host_tmp}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if scp.returncode != 0:
            raise RemoteError(scp.stderr.strip() or "scp upload failed")
        copied = self._ssh(f"docker cp {host_tmp} {self.container}:{container_dest}", timeout=300)
        if copied.returncode != 0:
            raise RemoteError(copied.stderr.strip() or "docker cp upload failed")


def _first_json(text: str) -> dict:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RemoteError(text.strip() or "no JSON from remote helper")


__all__ = ["RemoteStore", "RemoteError", "format_size", "DEFAULT_HOST"]
