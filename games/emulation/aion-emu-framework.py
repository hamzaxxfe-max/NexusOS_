#!/usr/bin/env python3
"""Aion Emulation Framework — zero-setup RetroArch PSX bridge.

Turns a ROM drop folder into a one-click console:
  * Ensures RetroArch + the PlayStation core are installed (downloading
    the pcsx_rearmed core from RetroArch's buildbot if missing).
  * Locates a PlayStation BIOS in the standard BIOS folder.
  * Configures RetroArch for gamepads, neon theme and scan-friendly
    settings on first run (read-only defaults are never overwritten).
  * Lists playable ROMs and launches them with one command.

The module is stdlib-only and every external action degrades gracefully:
  * no network? existing setup is still usable.
  * no core? ROM listing still works, launch explains the fix.

Usage (installed as /usr/local/bin/aion-emu):
  aion-emu status              — show RetroArch, core and BIOS state
  aion-emu setup               — install RetroArch + core + BIOS dir
  aion-emu list                — list playable ROMs
  aion-emu launch <rom>        — launch a ROM in RetroArch
  aion-emu cores-dir           — print the cores directory path

Environment overrides (used by tests):
  AION_EMU_CONFIG   RetroArch config dir   (default ~/.config/retroarch)
  AION_EMU_ROMDIR   ROM drop folder        (default ~/ROMs)
  AION_EMU_BIOSDIR  BIOS folder            (default ~/.config/retroarch/system)
  AION_EMU_COREDIR  Cores directory        (default <config>/cores)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()

CONFIG_DIR = Path(os.environ.get("AION_EMU_CONFIG", "~/.config/retroarch")).expanduser()
ROM_DIR = Path(os.environ.get("AION_EMU_ROMDIR", "~/ROMs")).expanduser()
BIOS_DIR = Path(os.environ.get("AION_EMU_BIOSDIR",
                               "~/.config/retroarch/system")).expanduser()
CORE_DIR = Path(os.environ.get("AION_EMU_COREDIR",
                               "~/.config/retroarch/cores")).expanduser()

# PlayStation core + known BIOS names.
PSX_CORE_NAME = "pcsx_rearmed"
PSX_CORE_FILE = f"{PSX_CORE_NAME}_libretro.so"
PSX_CORE_URL = (
    "https://buildbot.libretro.com/nightly/linux/x86_64/latest/"
    f"{PSX_CORE_FILE}"
)
BIOS_NAMES = (
    "scph1001.bin", "scph5501.bin", "scph5502.bin",
    "scph7001.bin", "scph7502.bin", "scph9001.bin",
    "ps1_rom.bin",
)
ROM_EXTENSIONS = (".cue", ".chd", ".pbp", ".iso", ".img", ".bin")

STATE_FILE = CONFIG_DIR / "aion-emu-state.json"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def _read_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def retroarch_bin() -> Optional[str]:
    return shutil.which("retroarch")


def core_installed() -> bool:
    return (CORE_DIR / PSX_CORE_FILE).is_file()


def bios_path() -> Optional[str]:
    for name in BIOS_NAMES:
        candidate = BIOS_DIR / name
        if candidate.is_file():
            return str(candidate)
    return None


def find_roms() -> List[Path]:
    if not ROM_DIR.is_dir():
        return []
    return sorted(
        p for p in ROM_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in ROM_EXTENSIONS
    )


def status() -> Dict[str, Any]:
    return {
        "retroarch": retroarch_bin() is not None,
        "core": core_installed(),
        "coreName": PSX_CORE_NAME,
        "bios": bios_path() is not None,
        "biosPath": bios_path(),
        "romDir": str(ROM_DIR),
        "romCount": len(find_roms()),
        "configured": (CONFIG_DIR / "retroarch.cfg").is_file(),
    }


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def _run(cmd: List[str], timeout: int = 600) -> int:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode
    except FileNotFoundError:
        return 127
    except subprocess.TimeoutExpired:
        return 124


def _download(url: str, dest: Path) -> bool:
    """Download a file to `dest` atomically. Returns success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        tmp.write_bytes(data)
        tmp.rename(dest)
        return True
    except (OSError, urllib.error.URLError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def setup_retroarch() -> bool:
    """Install RetroArch via the system package manager if possible."""
    if retroarch_bin():
        return True
    for pm, args in (
        ("pacman", ["pacman", "--noconfirm", "--needed", "-S", "retroarch"]),
        ("apt", ["apt-get", "install", "-y", "retroarch"]),
    ):
        if shutil.which(pm):
            return _run(args) == 0
    return False


def setup_core(force: bool = False) -> bool:
    """Download the PlayStation core if missing. Returns installed state."""
    if core_installed() and not force:
        return True
    return _download(PSX_CORE_URL, CORE_DIR / PSX_CORE_FILE)


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, ROM_DIR, BIOS_DIR, CORE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def write_default_config() -> bool:
    """Write a minimal neon-friendly config only if none exists yet."""
    cfg = CONFIG_DIR / "retroarch.cfg"
    if cfg.is_file():
        return False
    ensure_dirs()
    try:
        cfg.write_text(
            "# Aion Emulation Framework defaults\n"
            "video_driver = \"gl\"\n"
            "video_threaded = \"true\"\n"
            "audio_driver = \"pipewire\"\n"
            "menu_driver = \"ozone\"\n"
            "menu_wallpaper = \"\"\n"
            "input_autodetect_enable = \"true\"\n"
            "input_player1_joypad_index = \"0\"\n"
            "savestate_directory = \"~/.config/retroarch/saves\"\n"
            "savefile_directory = \"~/.config/retroarch/saves\"\n"
            "libretro_directory = \"~/.config/retroarch/cores\"\n"
            "system_directory = \"~/.config/retroarch/system\"\n"
            "cheevos_enable = \"false\"\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def run_setup(force_core: bool = False) -> Dict[str, Any]:
    ensure_dirs()
    ra_ok = setup_retroarch()
    core_ok = setup_core(force=force_core)
    configured = write_default_config()
    state = {
        "retroarch": ra_ok,
        "core": core_ok,
        "configured": configured,
        "bios": bios_path() is not None,
    }
    _write_state(state)
    return state


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
def launch_rom(rom: str) -> Dict[str, Any]:
    ra = retroarch_bin()
    if ra is None:
        return {"ok": False, "error": "RetroArch is not installed — run: aion-emu setup"}
    if not core_installed():
        return {"ok": False, "error": "PSX core missing — run: aion-emu setup"}

    matches = [p for p in find_roms() if p.name.lower() == rom.lower()]
    if not matches:
        matches = [p for p in find_roms() if rom.lower() in p.stem.lower()]
    if not matches:
        return {"ok": False, "error": f"ROM not found: {rom}"}

    target = matches[0]
    bios = bios_path()
    if bios is None:
        return {
            "ok": False,
            "error": "No PlayStation BIOS found in " + str(BIOS_DIR),
        }

    cmd = [
        ra, "-L", str(CORE_DIR / PSX_CORE_FILE), str(target),
        "--set", f"system_directory={BIOS_DIR}",
    ]
    code = _run(cmd)
    return {"ok": code == 0, "cmd": cmd, "exitCode": code}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt_status(s: Dict[str, Any]) -> str:
    def mark(ok: bool) -> str:
        return "OK " if ok else "-- "
    lines = [
        f"{mark(s['retroarch'])} RetroArch",
        f"{mark(s['core'])} PSX core ({s['coreName']})",
        f"{mark(s['bios'])} BIOS" + (f" @ {s['biosPath']}" if s["biosPath"] else ""),
        f"{mark(s['romCount'] > 0)} ROMs ({s['romCount']} found in {s['romDir']})",
        f"{mark(s['configured'])} Default config written",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aion Emulation Framework")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show RetroArch / core / BIOS state")
    sub.add_parser("setup", help="Install RetroArch + PSX core + config")
    sub.add_parser("list", help="List playable ROMs")
    launch = sub.add_parser("launch", help="Launch a ROM")
    launch.add_argument("rom", help="ROM filename or name fragment")
    cores = sub.add_parser("cores-dir", help="Print cores directory")
    cores.add_argument("--no-newline", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "status":
        print(_fmt_status(status()))
    elif args.command == "setup":
        state = run_setup(force_core=False)
        print(_fmt_status({
            "retroarch": state["retroarch"],
            "core": state["core"],
            "coreName": PSX_CORE_NAME,
            "bios": state["bios"],
            "biosPath": bios_path(),
            "romCount": len(find_roms()),
            "romDir": str(ROM_DIR),
            "configured": state["configured"],
        }))
    elif args.command == "list":
        roms = find_roms()
        for rom in roms:
            print(rom.name)
        print(f"\n{len(roms)} ROM(s) in {ROM_DIR}")
    elif args.command == "launch":
        result = launch_rom(args.rom)
        if result["ok"]:
            print("Launching…")
        else:
            print("Error:", result["error"])
            return 1
    elif args.command == "cores-dir":
        print(str(CORE_DIR), end="" if args.no_newline else "\n")
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
