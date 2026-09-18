"""Local UX session player. Serves repo /static so pulled recordings can replay."""

from __future__ import annotations

import mimetypes
import os
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from jinja2 import Environment, FileSystemLoader

from wellnav.db import ROOT
from wellnav.recordings import list_recordings, local_watch_dir, recording_file

TEMPLATES = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True)


def configure_watch_dir(folder: str | Path | None = None) -> Path:
    if folder:
        path = Path(folder).resolve()
    elif (os.environ.get("WELLNAV_RECORDINGS_DIR") or "").strip():
        path = Path(os.environ["WELLNAV_RECORDINGS_DIR"]).resolve()
    else:
        path = local_watch_dir().resolve()
    path.mkdir(parents=True, exist_ok=True)
    os.environ["WELLNAV_RECORDINGS_DIR"] = str(path)
    return path


def _safe_file(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _free_port(start: int = 8766) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return start


class ReplayHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in {"/", "/account"}:
            self._html(
                "ux_library.html",
                recordings=list_recordings(),
                watch_dir=str(local_watch_dir()),
                local_player=True,
            )
            return
        if path.startswith("/static/"):
            self._file(ROOT / "static", path[len("/static/") :])
            return
        if path.startswith("/watch/"):
            self._watch(path.split("/watch/", 1)[1])
            return
        if path.startswith("/ux/recordings/") and path.endswith("/file"):
            self._events(path[len("/ux/recordings/") : -len("/file")])
            return
        if path.startswith("/ux/recordings/"):
            self._watch(path[len("/ux/recordings/") :])
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _watch(self, recording_id: str) -> None:
        found = recording_file(recording_id)
        if not found:
            self._send(404, b"recording not found", "text/plain; charset=utf-8")
            return
        _path, meta = found
        self._html("ux_replay.html", recording=meta, local_player=True)

    def _events(self, recording_id: str) -> None:
        found = recording_file(recording_id)
        if not found:
            self._send(404, b"recording not found", "text/plain; charset=utf-8")
            return
        path, _meta = found
        media = "application/x-ndjson" if path.suffix == ".jsonl" else "video/webm"
        self._bytes(path.read_bytes(), media)

    def _html(self, name: str, **values: object) -> None:
        html = TEMPLATES.get_template(name).render(**values)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _file(self, root: Path, relative: str) -> None:
        path = _safe_file(root, relative)
        if not path:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        media, _enc = mimetypes.guess_type(path.name)
        self._bytes(path.read_bytes(), media or "application/octet-stream")

    def _bytes(self, body: bytes, media: str) -> None:
        self._send(200, body, media)

    def _send(self, status: int, body: bytes, media: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int | None = None, open_browser: bool = True) -> None:
    watch_dir = configure_watch_dir()
    bound = port or _free_port()
    url = f"http://{host}:{bound}/"
    print(f"watch_dir={watch_dir}")
    print(f"open {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except OSError:
            pass
    ThreadingHTTPServer((host, bound), ReplayHandler).serve_forever()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    configure_watch_dir(args[0] if args else None)
    serve()


if __name__ == "__main__":
    main()
