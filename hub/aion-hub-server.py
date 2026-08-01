#!/usr/bin/env python3
"""Aion Hub — one-click portal for applications, games and system tools.

A local-first web server (Python stdlib only) that unifies every install
source into a single, Windows-simple experience:

  * Steam, Epic, Lutris, Bottles, Heroic game launchers
  * pacman / flatpak system packages
  * Aion-native tools and drivers

Endpoints (JSON):
  GET  /api/apps                — catalog from manifest + installed state
  GET  /api/apps/<id>           — single app detail
  GET  /api/search?q=<term>     — catalog search
  POST /api/install/<id>        — one-click install (async)
  POST /api/uninstall/<id>      — remove an app
  GET  /api/status              — install queue status
  GET  /api/health              — server liveness

Run:  python3 aion-hub-server.py [--port 8931]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_FILE = Path("/var/log/aion/hub.log")
HUB_ROOT = Path(__file__).resolve().parent
MANIFEST = HUB_ROOT / "manifest.json"
WEB_DIR = HUB_ROOT / "web"
INSTALL_LOG = HUB_ROOT / "installer" / "install-queue.json"
PASSWORD_FILE = Path(os.environ.get(
    "AION_CONFIG_DIR", "/etc/aion")) / "hub-password.json"
DEFAULT_PORT = 8931

logger = logging.getLogger("aion-hub")

# --- Rate limiting ---------------------------------------------------------
# Budget per client for sensitive endpoints (auth brute force, install spam).
RATE_WINDOW = 60  # seconds
RATE_BUDGET = {
    "/api/auth/check": 5,   # 5 password guesses / minute
    "/api/install": 10,     # 10 installs / minute
}
_rate_lock = threading.Lock()
_rate_buckets: Dict[str, List[float]] = defaultdict(list)

# Allowed local origins (host header / Origin header must be loopback).
_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def _allow_request(path: str, client: str) -> bool:
    """Return True if `client` has budget left for `path` this window."""
    bucket = None
    for key, limit in RATE_BUDGET.items():
        if path == key or path.startswith(key):
            bucket = key
            break
    if bucket is None:
        return True
    with _rate_lock:
        now = time.monotonic()
        stamps = _rate_buckets[client]
        stamps[:] = [t for t in stamps if now - t < RATE_WINDOW]
        if len(stamps) >= RATE_BUDGET[bucket]:
            return False
        stamps.append(now)
        return True


def _local_peer(headers) -> bool:
    """Reject cross-origin / DNS-rebinding requests: the Host header must
    be a loopback name and (for POST) Origin must be a loopback URL."""
    host = (headers.get("Host") or "").strip()
    if not host:
        return False
    host_name = host.rsplit(":", 1)[0].strip("[]").lower()
    if host_name not in _ALLOWED_HOSTS:
        return False
    origin = headers.get("Origin")
    if origin:
        try:
            origin_host = urllib.parse.urlsplit(origin).netloc
            origin_host = origin_host.rsplit(":", 1)[0].strip("[]").lower()
        except ValueError:
            return False
        if origin_host not in _ALLOWED_HOSTS:
            return False
    return True


def _password_enabled() -> bool:
    return PASSWORD_FILE.is_file()


def _check_secret(secret: str) -> bool:
    """Verify a submitted secret against the optional hub password."""
    if not _password_enabled():
        return True
    try:
        from aion_hub_pass import verify
    except ImportError:
        spec = importlib.util.spec_from_file_location(
            "aion_hub_pass", HUB_ROOT / "aion-hub-pass.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.verify(secret, PASSWORD_FILE)
    return verify(secret, PASSWORD_FILE)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class Catalog:
    """Loads the unified app/game manifest and resolves install state."""

    def __init__(self, manifest_path: Path = MANIFEST):
        self.manifest_path = manifest_path
        self._apps: Dict[str, Dict[str, Any]] = {}
        self._reload()

    def _reload(self) -> None:
        if not self.manifest_path.is_file():
            self._apps = {}
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            for app in data.get("apps", []):
                app_id = app.get("id", "")
                if app_id:
                    self._apps[app_id] = app
        except (OSError, ValueError) as exc:
            logger.error("Manifest load failed: %s", exc)
            self._apps = {}

    @property
    def count(self) -> int:
        return len(self._apps)

    def all(self) -> List[Dict[str, Any]]:
        return [dict(app) for app in self._apps.values()]

    def get(self, app_id: str) -> Optional[Dict[str, Any]]:
        return self._apps.get(app_id)

    def search(self, query: str) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return self.all()
        return [
            dict(a) for a in self._apps.values()
            if q in a.get("name", "").lower()
            or q in a.get("category", "").lower()
            or q in " ".join(a.get("tags", [])).lower()
        ]

    def categories(self) -> List[str]:
        return sorted({a.get("category", "other") for a in self._apps.values()})


# ---------------------------------------------------------------------------
# Installer backend
# ---------------------------------------------------------------------------
class Installer:
    """Runs one-click install commands safely and tracks a background queue."""

    def __init__(self, queue_path: Path = INSTALL_LOG):
        self._lock = threading.Lock()
        self._queue: List[Dict[str, Any]] = []
        self._queue_path = queue_path
        self._load_queue()

    def _load_queue(self) -> None:
        try:
            if self._queue_path.is_file():
                self._queue = json.loads(self._queue_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._queue = []

    def _save_queue(self) -> None:
        try:
            self._queue_path.write_text(
                json.dumps(self._queue, indent=2), encoding="utf-8")
        except OSError:
            pass

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {"items": [dict(i) for i in self._queue],
                    "running": any(i["status"] == "running"
                                   for i in self._queue)}

    def _append(self, app_id: str) -> None:
        with self._lock:
            self._queue.append({
                "id": app_id,
                "status": "queued",
                "message": "",
            })
            self._save_queue()

    def _set_status(self, app_id: str, status: str, message: str = "") -> None:
        with self._lock:
            for item in self._queue:
                if item["id"] == app_id:
                    item["status"] = status
                    item["message"] = message
                    break
            self._save_queue()

    def enqueue(self, app: Dict[str, Any]) -> bool:
        if self._queue and any(i["id"] == app["id"] and
                               i["status"] in ("queued", "running")
                               for i in self._queue):
            return False
        self._append(app["id"])
        thread = threading.Thread(
            target=self._worker, args=(app,), daemon=True)
        thread.start()
        return True

    def _worker(self, app: Dict[str, Any]) -> None:
        self._set_status(app["id"], "running", "Installing…")
        try:
            code = run_install(app)
            if code == 0:
                self._set_status(app["id"], "done", "Installed")
            else:
                self._set_status(app["id"], "failed",
                                 f"exit code {code}")
        except Exception as exc:  # noqa: BLE001
            self._set_status(app["id"], "failed", str(exc))
            logger.exception("Install worker failed for %s", app["id"])


def run_install(app: Dict[str, Any], timeout: int = 1200) -> int:
    """Executes the app's install command. Returns process exit code."""
    cmd = app.get("install")
    if not isinstance(cmd, list) or not cmd:
        logger.warning("App %s has no install command", app.get("id"))
        return 2

    provider = app.get("provider", "aion")
    if provider == "steam":
        return run_steam(app, cmd, timeout)
    if provider == "epic":
        return run_epic(app, cmd, timeout)
    if provider == "pacman":
        return _run(["sudo", "pacman", "--noconfirm", "-S", *cmd], timeout)
    if provider == "flatpak":
        return _run(["flatpak", "install", "--assumeyes", "-y", *cmd], timeout)
    if provider in ("lutris", "bottles", "heroic", "aion"):
        return _run(cmd, timeout)
    return _run(cmd, timeout)


def run_steam(app: Dict[str, Any], cmd: List[str], timeout: int) -> int:
    """Installs a game via steamcmd (headless Steam client)."""
    appid = cmd[0] if cmd else ""
    user = os.environ.get("AION_STEAM_USER", "anonymous")
    password = os.environ.get("AION_STEAM_PASS", "")
    login = user if user != "anonymous" else "anonymous"
    script = [
        "steamcmd", "+force_install_dir", "/opt/aion/games",
        "+login", login,
    ]
    if password:
        script.append(password)
    script += ["+app_update", appid, "validate", "+quit"]
    return _run(script, timeout)


def run_epic(app: Dict[str, Any], cmd: List[str], timeout: int) -> int:
    """Installs an Epic title via legendary/heroic CLI if available."""
    app_name = cmd[0] if cmd else ""
    return _run(["legendary", "install", app_name], timeout)


def _run(cmd: List[str], timeout: int) -> int:
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.warning("cmd=%s rc=%s stderr=%s",
                           cmd[0], result.returncode,
                           result.stderr[-800:] if result.stderr else "")
        return result.returncode
    except FileNotFoundError:
        logger.warning("Command not found: %s", cmd[0])
        return 127
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out: %s", cmd[0])
        return 124


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class HubHandler(BaseHTTPRequestHandler):
    catalog: Catalog = Catalog()
    installer: Installer = Installer()

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        ext = path.suffix.lower()
        ctype = MIME.get(ext, "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_ui(self) -> None:
        requested = urllib.parse.unquote(self.path.split("?", 1)[0])
        rel = requested.lstrip("/")
        if not rel:
            rel = "index.html"
        target = (WEB_DIR / rel).resolve()
        if target.is_file() and str(target).startswith(str(WEB_DIR)):
            self._send_file(target)
            return
        fallback = (WEB_DIR / "index.html").resolve()
        if fallback.is_file():
            self._send_file(fallback)
            return
        self._send_json({"error": "web assets not deployed"}, 500)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if not _local_peer(self.headers):
            return self._send_json({"error": "forbidden"}, 403)

        if path == "/api/health":
            return self._send_json({"ok": True, "apps": self.catalog.count})

        if path == "/api/apps":
            return self._send_json(self.catalog.all())

        if path == "/api/search":
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query).get("q", [""])[0]
            return self._send_json(self.catalog.search(query))

        if path == "/api/status":
            return self._send_json(self.installer.status())

        if path.startswith("/api/apps/"):
            app_id = path[len("/api/apps/"):]
            app = self.catalog.get(app_id)
            if app is None:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(app)

        if path.startswith("/api/") or path == "/api":
            return self._send_json({"error": "unknown endpoint"}, 404)

        return self._serve_ui()

    def _read_body(self, max_bytes: int = 64 * 1024) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > max_bytes:
            return b""
        return self.rfile.read(length)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            client = self.address_string()
        except (AttributeError, OSError):
            client = "127.0.0.1"

        if not _local_peer(self.headers):
            return self._send_json({"error": "forbidden"}, 403)

        if path == "/api/auth/check":
            if not _allow_request(path, client):
                return self._send_json({"error": "rate limited",
                                        "retryAfter": RATE_WINDOW}, 429)
            body = self._read_body()
            try:
                payload = json.loads(body or b"{}")
                secret = payload.get("secret", "")
            except ValueError:
                return self._send_json({"error": "bad body"}, 400)
            ok = _check_secret(str(secret))
            return self._send_json({
                "enabled": _password_enabled(),
                "ok": ok,
            }, 200 if ok else 401)

        if path.startswith("/api/install/"):
            if not _allow_request("/api/install", client):
                return self._send_json({"error": "rate limited",
                                        "retryAfter": RATE_WINDOW}, 429)
            app_id = path[len("/api/install/"):]
            app = self.catalog.get(app_id)
            if app is None:
                return self._send_json({"error": "not found"}, 404)

            if _password_enabled():
                body = self._read_body()
                try:
                    payload = json.loads(body or b"{}")
                    secret = payload.get("secret", "")
                except ValueError:
                    secret = ""
                if not _check_secret(str(secret)):
                    return self._send_json({"error": "unauthorized"}, 401)

            queued = self.installer.enqueue(app)
            return self._send_json(
                {"id": app_id, "queued": queued})

        if path.startswith("/api/uninstall/"):
            app_id = path[len("/api/uninstall/"):]
            return self._send_json(
                {"id": app_id, "uninstall": "not implemented"})

        return self._send_json({"error": "unknown endpoint"}, 404)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aion Hub server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    server = ThreadingHTTPServer((args.bind, args.port), HubHandler)
    logger.info("Aion Hub listening on http://%s:%d/ (%d apps in catalog)",
                args.bind, args.port, HubHandler.catalog.count)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Aion Hub stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
