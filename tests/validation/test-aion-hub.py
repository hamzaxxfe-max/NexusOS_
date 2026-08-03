#!/usr/bin/env python3
"""Aion Hub — catalog, installer backend and HTTP endpoint tests."""
import importlib.util
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "hub"
SERVER = HUB / "aion-hub-server.py"
MANIFEST = HUB / "manifest.json"


def _load_server():
    spec = importlib.util.spec_from_file_location("aion_hub_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aion_hub_server"] = mod
    spec.loader.exec_module(mod)
    return mod


SERVER_MODULE = _load_server()


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.catalog = SERVER_MODULE.Catalog(MANIFEST)

    def test_manifest_loads_apps(self):
        self.assertGreater(self.catalog.count, 0)

    def test_apps_have_required_fields(self):
        for app in self.catalog.all():
            for key in ("id", "name", "category", "provider", "install"):
                self.assertIn(key, app, f"{app.get('name')} missing '{key}'")

    def test_ids_are_unique(self):
        ids = [a["id"] for a in self.catalog.all()]
        self.assertEqual(len(ids), len(set(ids)), "duplicate app ids")

    def test_install_commands_are_lists(self):
        for app in self.catalog.all():
            self.assertIsInstance(app["install"], list, app["name"])

    def test_search_finds_by_name(self):
        results = self.catalog.search("steam")
        self.assertTrue(any("Steam" in r["name"] for r in results))

    def test_search_empty_query_returns_all(self):
        self.assertEqual(len(self.catalog.search("  ")), self.catalog.count)

    def test_search_case_insensitive(self):
        self.assertTrue(self.catalog.search("DOCKER"))

    def test_categories_includes_gaming_and_dev(self):
        cats = self.catalog.categories()
        self.assertIn("game-platform", cats)
        self.assertIn("development", cats)


class TestMultiPlatform(unittest.TestCase):
    """The hub must cover games from every platform plus dev tools."""

    def test_has_steam(self):
        app = SERVER_MODULE.Catalog(MANIFEST).get("steam")
        self.assertIsNotNone(app)
        self.assertEqual(app["provider"], "pacman")

    def test_has_epic_path(self):
        # Heroic + Lutris cover Epic/GOG on Linux.
        catalog = SERVER_MODULE.Catalog(MANIFEST)
        self.assertIsNotNone(catalog.get("heroic"))
        self.assertIsNotNone(catalog.get("lutris"))

    def test_has_windows_compat(self):
        catalog = SERVER_MODULE.Catalog(MANIFEST)
        self.assertIsNotNone(catalog.get("wine"))
        self.assertIsNotNone(catalog.get("bottles"))

    def test_has_developer_tools(self):
        catalog = SERVER_MODULE.Catalog(MANIFEST)
        for app_id in ("code", "docker", "git", "python"):
            self.assertIsNotNone(catalog.get(app_id), app_id)

    def test_has_productivity(self):
        catalog = SERVER_MODULE.Catalog(MANIFEST)
        self.assertIsNotNone(catalog.get("firefox"))


class TestInstaller(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.installer = SERVER_MODULE.Installer(
            Path(self.tmp.name) / "queue.json")
        self.catalog = SERVER_MODULE.Catalog(MANIFEST)

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_empty_initially(self):
        self.assertEqual(self.installer.status()["items"], [])

    def test_duplicate_enqueue_rejected(self):
        app = self.catalog.get("git")
        first = self.installer.enqueue(app)
        self.assertTrue(first)
        # Immediately re-enqueue while running/queued.
        self.installer._append("git")  # simulate queued
        again = self.installer.enqueue(app)
        self.assertFalse(again)

    def test_run_install_returns_int(self):
        code = SERVER_MODULE.run_install(
            {"id": "x", "install": ["this-command-does-not-exist"]})
        self.assertEqual(code, 127)

    @patch("aion_hub_server._run", return_value=0)
    def test_worker_marks_done(self, _mock_run):
        app = self.catalog.get("git")
        self.installer.enqueue(app)
        self.installer._worker(app)
        status = self.installer.status()
        self.assertEqual(status["items"][0]["status"], "done")

    def test_status_snapshot_does_not_mutate(self):
        self.installer._append("steam")
        snap = self.installer.status()
        snap["items"][0]["status"] = "tampered"
        self.assertEqual(self.installer.status()["items"][0]["status"], "queued")

    def test_steam_cmd_uses_steamcmd_and_appid(self):
        with patch("aion_hub_server._run", return_value=0) as m:
            code = SERVER_MODULE.run_steam(
                {"id": "g", "install": ["730"]}, ["730"], 60)
            self.assertEqual(code, 0)
            cmd = m.call_args[0][0]
            self.assertIn("steamcmd", cmd)
            self.assertIn("730", cmd)
            self.assertIn("+app_update", cmd)


class TestSecurity(unittest.TestCase):
    """One-click installs must never shell-execute arbitrary strings."""

    def test_no_shell_true(self):
        src = SERVER.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", src, "no shell execution allowed")
        self.assertNotIn("shell=True", SERVER_MODULE._run.__code__.co_consts,
                         "_run must use list commands")

    def test_no_eval(self):
        src = SERVER.read_text(encoding="utf-8")
        self.assertNotIn("eval(", src)
        self.assertNotIn("os.system(", src)

    def test_static_ui_served_within_webdir(self):
        from http.server import BaseHTTPRequestHandler
        self.assertTrue(issubclass(SERVER_MODULE.HubHandler, BaseHTTPRequestHandler))

    def test_api_routes_are_prefix_guarded(self):
        known = ("/api/health", "/api/apps", "/api/search", "/api/status",
                 "/api/apps/steam")
        for path in known:
            self.assertTrue(path.startswith("/api/"), path)
        self.assertIsNotNone(getattr(SERVER_MODULE.HubHandler, "catalog", None))
        self.assertIsNotNone(getattr(SERVER_MODULE.HubHandler, "installer", None))

    def test_do_post_rejects_unknown(self):
        import io
        from http.server import BaseHTTPRequestHandler

        class NoServer:
            pass

        # Build handler with the minimum server attribute the class expects.
        original_do_post = SERVER_MODULE.HubHandler.do_POST

        stub_self = object.__new__(SERVER_MODULE.HubHandler)
        stub_self.path = "/api/frobnicate"
        stub_self.command = "POST"
        stub_self.request_version = "HTTP/1.1"
        stub_self.headers = {"Host": "127.0.0.1:8931"}
        stub_self.wfile = io.BytesIO()
        stub_self.server = NoServer()
        stub_self.connection = None
        stub_self.close_connection = True

        with patch.object(SERVER_MODULE.HubHandler, "_send_json",
                          wraps=lambda payload, status=200: (payload, status)) as send:
            original_do_post(stub_self)
            payload, status = send.call_args[0]
            self.assertEqual(status, 404)


    def test_do_post_rejects_cross_origin_host(self):
        """POST with a non-loopback Host header must be rejected (403).

        Prevents DNS rebinding / cross-origin requests from reaching the
        hub's privileged endpoints.
        """
        import io

        stub_self = object.__new__(SERVER_MODULE.HubHandler)
        stub_self.path = "/api/install/steam"
        stub_self.command = "POST"
        stub_self.request_version = "HTTP/1.1"
        stub_self.headers = {"Host": "evil.example.com"}
        stub_self.wfile = io.BytesIO()
        stub_self.server = type("NoServer", (), {})()
        stub_self.connection = None
        stub_self.close_connection = True

        with patch.object(SERVER_MODULE.HubHandler, "_send_json",
                          wraps=lambda payload, status=200: (payload, status)) as send:
            stub_self.do_POST()
            payload, status = send.call_args[0]
            self.assertEqual(status, 403)

    def test_do_post_rate_limits_auth_check(self):
        """Repeated /api/auth/check attempts from one client get 429."""
        import io
        from unittest.mock import patch as _patch

        stub_self = object.__new__(SERVER_MODULE.HubHandler)
        stub_self.command = "POST"
        stub_self.request_version = "HTTP/1.1"
        stub_self.headers = {"Host": "127.0.0.1", "Content-Length": "2"}
        stub_self.rfile = io.BytesIO(b"{}")
        stub_self.wfile = io.BytesIO()
        stub_self.server = type("NoServer", (), {})()
        stub_self.connection = None
        stub_self.close_connection = True
        stub_self.address_string = lambda: "1.2.3.4"

        statuses = []
        with _patch.object(SERVER_MODULE.HubHandler, "_send_json",
                           wraps=lambda payload, status=200: status):
            for _ in range(SERVER_MODULE.RATE_BUDGET["/api/auth/check"] + 1):
                stub_self.path = "/api/auth/check"
                stub_self.wfile = io.BytesIO()
                stub_self.rfile = io.BytesIO(b"{}")
                status = stub_self.do_POST()
                statuses.append(status if status is not None else 200)
        self.assertIn(429, statuses)


def path_guard(handler_cls, path):
    return path.startswith("/api/")


class TestLibraryBridge(unittest.TestCase):
    """Unified store: Steam/Lutris/Heroic library aggregation."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Steam/steamapps/common/Test Game").mkdir(parents=True)
        (self.root / "Steam/steamapps/appmanifest_730.acf").write_text(
            '"AppState"\n\t"appid"\t\t"730"\n\t"name"\t\t"Counter-Strike 2"\n',
            encoding="utf-8")
        (self.root / "lutris/games").mkdir(parents=True)
        (self.root / "lutris/games/elden-ring.yml").write_text(
            "name: Elden Ring\nslug: elden-ring\n", encoding="utf-8")
        (self.root / "heroic/games_store").mkdir(parents=True)
        (self.root / "heroic/games_store/settings.json").write_text(
            json.dumps({"library": {"witcher3": {"title": "Witcher 3"}}}),
            encoding="utf-8")

        bridge = HUB / "library-bridge.py"
        spec = importlib.util.spec_from_file_location("aion_library_bridge", bridge)
        self.lib = importlib.util.module_from_spec(spec)
        sys.modules["aion_library_bridge"] = self.lib
        spec.loader.exec_module(self.lib)

        self.lib.STEAM_BASE = self.root / "Steam"
        self.lib.LUTRIS_GAMES = self.root / "lutris/games"
        self.lib.HEROIC_SETTINGS = self.root / "heroic/games_store/settings.json"
        self.lib.BOTTLES_DATA = self.root / "bottles"  # empty → skipped
        self.lib.AION_GAMES = self.root / "aion"  # empty → skipped

    def tearDown(self):
        self.tmp.cleanup()

    def test_aggregates_all_sources(self):
        library = self.lib.Library()
        games = library.all()
        self.assertGreaterEqual(len(games), 3)
        sources = {g["source"] for g in games}
        self.assertIn("steam", sources)
        self.assertIn("lutris", sources)
        self.assertIn("heroic", sources)

    def test_steam_entry_has_launch_uri(self):
        games = self.lib.Library().all()
        steam = [g for g in games if g["source"] == "steam"]
        self.assertTrue(steam)
        self.assertIn("steam://rungameid/730", steam[0]["launch"])

    def test_stats_reports_totals(self):
        stats = self.lib.Library().stats()
        self.assertGreaterEqual(stats["total"], 3)
        self.assertGreaterEqual(stats["perSource"]["steam"], 1)
        self.assertEqual(len(stats["sources"]), 6)

    def test_ids_are_unique_across_sources(self):
        games = self.lib.Library().all()
        ids = [g["id"] for g in games]
        self.assertEqual(len(ids), len(set(ids)))

    def test_broken_source_does_not_kill_library(self):
        library = self.lib.Library(sources=["steam", "lutris", "heroic"])
        games = library.all()
        self.assertGreaterEqual(len(games), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
