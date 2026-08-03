#!/usr/bin/env python3
"""Aion Unified Store — library aggregation bridge.

Scans locally-installed game libraries (Steam, Lutris, Heroic/Bottles,
Flatpak games, native Aion games) and exposes them as one unified list
so the Aion Hub can show "everything you own" without switching launchers.

Discovery is read-only and stdlib-only. Every source is optional: if the
launcher isn't installed or has no data, that source is simply skipped.

Endpoints consumed by the hub:
  GET /api/library            — aggregated installed games
  GET /api/library/stats      — per-source counts + total size

Sources:
  * Steam        — <~/.local/share/Steam/steamapps/*.acf> manifests
  * Lutris       — <~/.config/lutris/games/*.yml>
  * Heroic       — <~/.config/heroic/games_store/settings.json>
  * Bottles      — <~/.var/app/com.usebottles.bottles/data/bottles/>
  * Flatpak      — <flatpak list> via json output (optional binary)
  * Aion native  — <~/.local/share/aion/games/*.json>

No writes are performed; all paths are read-only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default locations (overridable via env for testing).
STEAM_BASE = Path(os.environ.get("AION_STEAM_BASE",
                                 "~/.local/share/Steam")).expanduser()
LUTRIS_GAMES = Path(os.environ.get("AION_LUTRIS_GAMES",
                                   "~/.config/lutris/games")).expanduser()
HEROIC_SETTINGS = Path(os.environ.get("AION_HEROIC_SETTINGS",
                                      "~/.config/heroic/games_store/settings.json")).expanduser()
BOTTLES_DATA = Path(os.environ.get("AION_BOTTLES_DATA",
                                   "~/.var/app/com.usebottles.bottles/data/bottles")).expanduser()
AION_GAMES = Path(os.environ.get("AION_NATIVE_GAMES",
                                 "~/.local/share/aion/games")).expanduser()

# Sizes below which a "game" is probably just an empty stub folder.
_MIN_MB = 1
_MB = 1024 * 1024


def _dir_size(path: Path) -> int:
    """Return total size in MB of a directory (best effort, bounded scan)."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
                if total > 200 * _MB:
                    return total // _MB
    except OSError:
        return 0
    return total // _MB


def _scan_steam() -> List[Dict[str, Any]]:
    """Parse Steam library .acf manifests for installed app entries."""
    games: List[Dict[str, Any]] = []
    steamapps = STEAM_BASE / "steamapps"
    if not steamapps.is_dir():
        return games
    for acf in sorted(steamapps.glob("appmanifest_*.acf")):
        text = ""
        try:
            text = acf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'"appid"\s+"(\d+)"', text)
        name = re.search(r'"name"\s+"([^"]+)"', text)
        if not m or not name:
            continue
        appid, title = m.group(1), name.group(1)
        size = _dir_size(steamapps / f"common/{title}") if title else 0
        games.append({
            "id": f"steam:{appid}",
            "name": title,
            "source": "steam",
            "launch": ["steam", f"steam://rungameid/{appid}"],
            "sizeMb": size,
        })
    return games


def _scan_lutris() -> List[Dict[str, Any]]:
    """Parse Lutris game YAMLs for installed entries."""
    games: List[Dict[str, Any]] = []
    if not LUTRIS_GAMES.is_dir():
        return games
    for yml in sorted(LUTRIS_GAMES.glob("*.yml")):
        try:
            text = yml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'^\s*name:\s*["\']?([^"\'\n]+)', text, re.MULTILINE)
        slug = yml.stem
        games.append({
            "id": f"lutris:{slug}",
            "name": (m.group(1).strip() if m else slug),
            "source": "lutris",
            "launch": ["lutris", f"lutris:rungame/{slug}"],
            "sizeMb": _dir_size(yml.parent),
        })
    return games


def _scan_heroic() -> List[Dict[str, Any]]:
    """Parse Heroic's games_store settings for installed titles."""
    games: List[Dict[str, Any]] = []
    if not HEROIC_SETTINGS.is_file():
        return games
    try:
        data = json.loads(HEROIC_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return games
    store = data.get("library") or data.get("games") or {}
    if not isinstance(store, dict):
        store = {}
    for app_name, entry in store.items():
        if not isinstance(entry, dict):
            continue
        title = entry.get("title") or entry.get("app_name") or app_name
        games.append({
            "id": f"heroic:{app_name}",
            "name": title,
            "source": "heroic",
            "launch": ["heroic", f"heroic://launch/{app_name}"],
            "sizeMb": 0,
        })
    return games


def _scan_bottles() -> List[Dict[str, Any]]:
    """List Bottles containers as playable game bottles."""
    games: List[Dict[str, Any]] = []
    if not BOTTLES_DATA.is_dir():
        return games
    for bottle in sorted(BOTTLES_DATA.iterdir()):
        if not bottle.is_dir() or bottle.name.startswith("."):
            continue
        games.append({
            "id": f"bottles:{bottle.name}",
            "name": bottle.name,
            "source": "bottles",
            "launch": ["flatpak", "run", "com.usebottles.bottles",
                       f"bottles:launch/{bottle.name}"],
            "sizeMb": _dir_size(bottle),
        })
    return games


def _scan_aion_native() -> List[Dict[str, Any]]:
    """List Aion-native game stubs declared as JSON manifests."""
    games: List[Dict[str, Any]] = []
    if not AION_GAMES.is_dir():
        return games
    for manifest in sorted(AION_GAMES.glob("*.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        title = data.get("name", manifest.stem)
        app_id = data.get("appid", manifest.stem)
        games.append({
            "id": f"aion:{app_id}",
            "name": title,
            "source": "aion",
            "launch": data.get("launch") or ["aion-run", app_id],
            "sizeMb": data.get("sizeMb", 0),
        })
    return games


def _scan_flatpak() -> List[Dict[str, Any]]:
    """List installed Flatpak games via `flatpak list --app` (optional)."""
    games: List[Dict[str, Any]] = []
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return games
    try:
        result = subprocess.run(
            [flatpak, "list", "--app", "--columns=application,name"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return games
    if result.returncode != 0:
        return games
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        app_id, title = parts[0].strip(), parts[1].strip()
        if ".Game." not in app_id and "games" not in title.lower():
            continue
        games.append({
            "id": f"flatpak:{app_id}",
            "name": title or app_id,
            "source": "flatpak",
            "launch": ["flatpak", "run", app_id],
            "sizeMb": 0,
        })
    return games


SCANNERS: Dict[str, Any] = {
    "steam": _scan_steam,
    "lutris": _scan_lutris,
    "heroic": _scan_heroic,
    "bottles": _scan_bottles,
    "aion": _scan_aion_native,
    "flatpak": _scan_flatpak,
}


class Library:
    """Aggregated view of every detected local game library."""

    def __init__(self, sources: Optional[List[str]] = None):
        self.sources = sources or list(SCANNERS.keys())

    def _scan(self, source: str) -> List[Dict[str, Any]]:
        scanner = SCANNERS.get(source)
        if scanner is None:
            return []
        try:
            return scanner()
        except Exception:  # noqa: BLE001 - one broken source must not kill the hub
            return []

    def all(self) -> List[Dict[str, Any]]:
        games: List[Dict[str, Any]] = []
        seen: Dict[str, bool] = {}
        for source in self.sources:
            for game in self._scan(source):
                if game["id"] in seen:
                    continue
                seen[game["id"]] = True
                if game.get("sizeMb", 0) < _MIN_MB and source in ("steam", "bottles"):
                    game["sizeMb"] = 0
                games.append(game)
        games.sort(key=lambda g: (g["name"] or "").lower())
        return games

    def stats(self) -> Dict[str, Any]:
        games = self.all()
        per_source: Dict[str, int] = {}
        total_mb = 0
        for game in games:
            source = game["source"]
            per_source[source] = per_source.get(source, 0) + 1
            total_mb += game.get("sizeMb", 0)
        return {
            "total": len(games),
            "totalSizeMb": total_mb,
            "perSource": per_source,
            "sources": self.sources,
        }
