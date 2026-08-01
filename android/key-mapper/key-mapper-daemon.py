#!/usr/bin/env python3
import sys
import os
import json
import time
import signal
import logging
import threading
import struct
import fcntl
import subprocess
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

LOG_DIR = Path("/var/log/aion")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "key-mapper.log"

CONFIG_DIR = Path.home() / ".config" / "aion"
KEYMAPS_FILE = CONFIG_DIR / "keymaps.json"
# Offline-only: no remote profile fetching. The daemon must not initiate
# any network egress (see aion-key-mapper.service PrivateNetwork=true).

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("key-mapper")

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03

SYN_REPORT = 0
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_PRESSURE = 0x3a
ABS_MT_SLOT = 0x2f
ABS_X = 0x00
ABS_Y = 0x01
ABS_RX = 0x39
ABS_RY = 0x3a

KEY_W = 17
KEY_A = 30
KEY_S = 31
KEY_D = 32
KEY_SPACE = 57
KEY_LEFTSHIFT = 42
KEY_LEFTCTRL = 29
KEY_TAB = 15
KEY_R = 19
KEY_E = 18
KEY_F = 33
KEY_Q = 16
KEY_J = 36
KEY_K = 37
KEY_L = 38
KEY_SEMICOLON = 39

BTN_LEFT = 272
BTN_RIGHT = 274
BTN_MIDDLE = 273

REL_X = 0
REL_Y = 1
REL_WHEEL = 8

IOCTL_UI_SET_EVBIT = 0x40045564
IOCTL_UI_SET_KEYBIT = 0x40045565
IOCTL_UI_SET_RELBIT = 0x40045566
IOCTL_UI_SET_ABSBIT = 0x40045567
IOCTL_UI_DEV_CREATE = 0x5501
IOCTL_UI_DEV_DESTROY = 0x5502
IOCTL_UI_SET_ABSINFO = 0x400c556a

input_event_format = "llHHi"


@dataclass
class TouchZone:
    x_min: float = 0.0
    x_max: float = 1.0
    y_min: float = 0.0
    y_max: float = 1.0
    action: str = "none"
    binding: str = ""
    sensitivity: float = 1.0
    deadzone: float = 0.05


@dataclass
class KeyMappingProfile:
    name: str = "default"
    package: str = ""
    description: str = ""
    screen_width: int = 1280
    screen_height: int = 720
    zones: list = field(default_factory=list)
    key_bindings: dict = field(default_factory=dict)
    touch_to_mouse: bool = True
    left_stick_zone: Optional[TouchZone] = None
    right_stick_zone: Optional[TouchZone] = None


@dataclass
class TouchPoint:
    slot: int = 0
    tracking_id: int = -1
    x: float = 0.0
    y: float = 0.0
    pressure: float = 0.0
    is_down: bool = False
    start_x: float = 0.0
    start_y: float = 0.0
    swipe_dx: float = 0.0
    swipe_dy: float = 0.0


class UInputDevice:
    def __init__(self, name: str, bustype: int = 0x03, vendor: int = 0x1234,
                 product: int = 0x5678, version: int = 1):
        self.name = name.encode("utf-8")[:80]
        self.bustype = bustype
        self.vendor = vendor
        self.product = product
        self.version = version
        self.fd = -1

    def _create_device_struct(self):
        buf = bytearray(548)
        buf[: len(self.name)] = self.name
        struct.pack_into("H", buf, 52, self.bustype)
        struct.pack_into("H", buf, 54, self.vendor)
        struct.pack_into("H", buf, 56, self.product)
        struct.pack_into("I", buf, 58, self.version)
        return buf

    def open(self):
        try:
            self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            self.fd = os.open("/dev/input/uinput", os.O_WRONLY | os.O_NONBLOCK)
        dev_struct = self._create_device_struct()
        fcntl.ioctl(self.fd, IOCTL_UI_DEV_CREATE, dev_struct)
        logger.info("Created uinput device: %s", self.name.decode(errors="replace"))
        return self

    def set_evbit(self, ev_type):
        try:
            fcntl.ioctl(self.fd, IOCTL_UI_SET_EVBIT, ev_type)
        except OSError as e:
            logger.warning("set_evbit(%d) failed: %s", ev_type, e)

    def set_keybit(self, key):
        try:
            fcntl.ioctl(self.fd, IOCTL_UI_SET_KEYBIT, key)
        except OSError as e:
            logger.warning("set_keybit(%d) failed: %s", key, e)

    def set_relbit(self, rel):
        try:
            fcntl.ioctl(self.fd, IOCTL_UI_SET_RELBIT, rel)
        except OSError as e:
            logger.warning("set_relbit(%d) failed: %s", rel, e)

    def set_absbit(self, axis, max_val, fuzz=0, flat=0, resolution=0):
        absinfo = struct.pack("iiiiii", 0, max_val, fuzz, flat, resolution, 0)
        try:
            fcntl.ioctl(self.fd, IOCTL_UI_SET_ABSBIT, axis)
            fcntl.ioctl(self.fd, IOCTL_UI_SET_ABSINFO, absinfo)
        except OSError as e:
            logger.warning("set_absbit(%d) failed: %s", axis, e)

    def emit_event(self, event_type, code, value):
        tv_sec = int(time.time())
        tv_usec = int((time.time() % 1) * 1000000)
        event = struct.pack(input_event_format, tv_sec, tv_usec, event_type, code, value)
        try:
            os.write(self.fd, event)
        except OSError as e:
            logger.error("emit_event failed: %s", e)

    def syn(self):
        self.emit_event(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self.fd >= 0:
            try:
                fcntl.ioctl(self.fd, IOCTL_UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


class KeyMapperDaemon:
    def __init__(self):
        self.running = False
        self.mouse_device = None
        self.keyboard_device = None
        self.gamepad_device = None
        self.profiles = {}
        self.current_profile = None
        self.current_package = ""
        self.touch_points = {}
        self.lock = threading.Lock()
        self._swipe_threshold = 30.0

    def _load_profiles(self):
        KEYMAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if KEYMAPS_FILE.exists():
            try:
                raw = json.loads(KEYMAPS_FILE.read_text(encoding="utf-8"))
                for key, profile_data in raw.get("profiles", {}).items():
                    self.profiles[key] = self._parse_profile(key, profile_data)
                logger.info("Loaded %d profiles", len(self.profiles))
            except (json.JSONDecodeError, IOError) as e:
                logger.error("Failed to load profiles: %s", e)
        else:
            self._create_default_profiles()

    def _parse_profile(self, key, data):
        profile = KeyMappingProfile(
            name=data.get("name", key),
            package=data.get("package", ""),
            description=data.get("description", ""),
            screen_width=data.get("screen_width", 1280),
            screen_height=data.get("screen_height", 720),
            touch_to_mouse=data.get("touch_to_mouse", True),
        )
        for zd in data.get("zones", []):
            zone = TouchZone(
                x_min=zd.get("x_min", 0),
                x_max=zd.get("x_max", 1),
                y_min=zd.get("y_min", 0),
                y_max=zd.get("y_max", 1),
                action=zd.get("action", "none"),
                binding=zd.get("binding", ""),
                sensitivity=zd.get("sensitivity", 1.0),
                deadzone=zd.get("deadzone", 0.05),
            )
            profile.zones.append(zone)
        profile.key_bindings = data.get("key_bindings", {})
        ls = data.get("left_stick_zone")
        if ls:
            profile.left_stick_zone = TouchZone(
                x_min=ls.get("x_min", 0), x_max=ls.get("x_max", 0.3),
                y_min=ls.get("y_min", 0.6), y_max=ls.get("y_max", 1.0),
                action="left_stick", sensitivity=ls.get("sensitivity", 1.0),
            )
        rs = data.get("right_stick_zone")
        if rs:
            profile.right_stick_zone = TouchZone(
                x_min=rs.get("x_min", 0.5), x_max=rs.get("x_max", 1.0),
                y_min=rs.get("y_min", 0.0), y_max=rs.get("y_max", 0.5),
                action="right_stick", sensitivity=rs.get("sensitivity", 1.0),
            )
        return profile

    def _create_default_profiles(self):
        defaults = {
            "fps": {
                "name": "FPS Default",
                "description": "First-Person Shooter template",
                "screen_width": 1280, "screen_height": 720, "touch_to_mouse": True,
                "left_stick_zone": {"x_min": 0.0, "x_max": 0.35, "y_min": 0.55, "y_max": 1.0, "sensitivity": 1.2},
                "right_stick_zone": {"x_min": 0.45, "x_max": 1.0, "y_min": 0.0, "y_max": 0.55, "sensitivity": 0.8},
                "zones": [
                    {"x_min": 0.85, "x_max": 1.0, "y_min": 0.7, "y_max": 0.85, "action": "tap", "binding": "KEY_LEFTCTRL"},
                    {"x_min": 0.85, "x_max": 1.0, "y_min": 0.85, "y_max": 1.0, "action": "tap", "binding": "KEY_SPACE"},
                    {"x_min": 0.75, "x_max": 0.85, "y_min": 0.7, "y_max": 0.85, "action": "tap", "binding": "KEY_R"},
                    {"x_min": 0.75, "x_max": 0.85, "y_min": 0.85, "y_max": 1.0, "action": "tap", "binding": "KEY_E"},
                ],
                "key_bindings": {
                    "KEY_W": "move_up", "KEY_S": "move_down",
                    "KEY_A": "move_left", "KEY_D": "move_right",
                    "KEY_SPACE": "jump", "KEY_LEFTSHIFT": "sprint",
                    "KEY_R": "reload", "KEY_F": "interact",
                    "KEY_TAB": "inventory", "KEY_Q": "ability",
                    "BTN_LEFT": "fire", "BTN_RIGHT": "aim",
                },
            },
            "moba": {
                "name": "MOBA Default",
                "description": "MOBA/RTS template",
                "screen_width": 1280, "screen_height": 720, "touch_to_mouse": True,
                "zones": [
                    {"x_min": 0.0, "x_max": 0.5, "y_min": 0.0, "y_max": 1.0, "action": "tap", "binding": "BTN_RIGHT"},
                    {"x_min": 0.6, "x_max": 0.75, "y_min": 0.75, "y_max": 0.875, "action": "tap", "binding": "KEY_Q"},
                    {"x_min": 0.75, "x_max": 0.875, "y_min": 0.75, "y_max": 0.875, "action": "tap", "binding": "KEY_W"},
                    {"x_min": 0.875, "x_max": 1.0, "y_min": 0.75, "y_max": 0.875, "action": "tap", "binding": "KEY_E"},
                    {"x_min": 0.6, "x_max": 0.75, "y_min": 0.875, "y_max": 1.0, "action": "tap", "binding": "KEY_R"},
                    {"x_min": 0.0, "x_max": 0.2, "y_min": 0.0, "y_max": 0.2, "action": "tap", "binding": "KEY_TAB"},
                ],
                "key_bindings": {
                    "BTN_RIGHT": "move_command",
                    "KEY_Q": "ability_1", "KEY_W": "ability_2",
                    "KEY_E": "ability_3", "KEY_R": "ultimate",
                    "KEY_TAB": "scoreboard",
                },
            },
            "racing": {
                "name": "Racing Default",
                "description": "Racing game template",
                "screen_width": 1280, "screen_height": 720, "touch_to_mouse": False,
                "zones": [
                    {"x_min": 0.0, "x_max": 0.5, "y_min": 0.3, "y_max": 0.7, "action": "tilt_steer", "sensitivity": 1.5, "deadzone": 0.1},
                    {"x_min": 0.6, "x_max": 1.0, "y_min": 0.5, "y_max": 1.0, "action": "tap", "binding": "KEY_W"},
                    {"x_min": 0.0, "x_max": 0.4, "y_min": 0.7, "y_max": 1.0, "action": "tap", "binding": "KEY_S"},
                    {"x_min": 0.6, "x_max": 1.0, "y_min": 0.0, "y_max": 0.3, "action": "tap", "binding": "KEY_SPACE"},
                ],
                "key_bindings": {
                    "KEY_A": "steer_left", "KEY_D": "steer_right",
                    "KEY_W": "gas", "KEY_S": "brake",
                    "KEY_SPACE": "handbrake",
                },
            },
            "rpg": {
                "name": "RPG Default",
                "description": "RPG/JRPG template",
                "screen_width": 1280, "screen_height": 720, "touch_to_mouse": True,
                "left_stick_zone": {"x_min": 0.0, "x_max": 0.3, "y_min": 0.5, "y_max": 1.0, "sensitivity": 1.0},
                "zones": [
                    {"x_min": 0.75, "x_max": 0.875, "y_min": 0.6, "y_max": 0.75, "action": "tap", "binding": "KEY_J"},
                    {"x_min": 0.875, "x_max": 1.0, "y_min": 0.6, "y_max": 0.75, "action": "tap", "binding": "KEY_K"},
                    {"x_min": 0.75, "x_max": 0.875, "y_min": 0.75, "y_max": 0.9, "action": "tap", "binding": "KEY_L"},
                    {"x_min": 0.875, "x_max": 1.0, "y_min": 0.75, "y_max": 0.9, "action": "tap", "binding": "KEY_SEMICOLON"},
                    {"x_min": 0.75, "x_max": 1.0, "y_min": 0.9, "y_max": 1.0, "action": "tap", "binding": "KEY_E"},
                    {"x_min": 0.5, "x_max": 0.6, "y_min": 0.8, "y_max": 0.95, "action": "tap", "binding": "KEY_SPACE"},
                    {"x_min": 0.4, "x_max": 0.5, "y_min": 0.8, "y_max": 0.95, "action": "tap", "binding": "KEY_TAB"},
                ],
                "key_bindings": {
                    "KEY_W": "move_up", "KEY_S": "move_down",
                    "KEY_A": "move_left", "KEY_D": "move_right",
                    "KEY_J": "attack", "KEY_K": "magic",
                    "KEY_L": "defend", "KEY_E": "interact",
                    "KEY_TAB": "menu", "KEY_SPACE": "confirm",
                },
            },
        }
        config = {"profiles": defaults}
        KEYMAPS_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
        for key, data in defaults.items():
            self.profiles[key] = self._parse_profile(key, data)
        logger.info("Created default profiles")

    def _save_profiles(self):
        data = {"profiles": {}}
        for key, p in self.profiles.items():
            zones_out = []
            for z in p.zones:
                zones_out.append({
                    "x_min": z.x_min, "x_max": z.x_max,
                    "y_min": z.y_min, "y_max": z.y_max,
                    "action": z.action, "binding": z.binding,
                    "sensitivity": z.sensitivity, "deadzone": z.deadzone,
                })
            entry = {
                "name": p.name, "package": p.package, "description": p.description,
                "screen_width": p.screen_width, "screen_height": p.screen_height,
                "touch_to_mouse": p.touch_to_mouse, "zones": zones_out,
                "key_bindings": p.key_bindings,
            }
            if p.left_stick_zone:
                z = p.left_stick_zone
                entry["left_stick_zone"] = {"x_min": z.x_min, "x_max": z.x_max, "y_min": z.y_min, "y_max": z.y_max, "sensitivity": z.sensitivity}
            if p.right_stick_zone:
                z = p.right_stick_zone
                entry["right_stick_zone"] = {"x_min": z.x_min, "x_max": z.x_max, "y_min": z.y_min, "y_max": z.y_max, "sensitivity": z.sensitivity}
            data["profiles"][key] = entry
        KEYMAPS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _setup_devices(self):
        self.mouse_device = UInputDevice("Aion Virtual Mouse")
        self.mouse_device.open()
        self.mouse_device.set_evbit(EV_KEY)
        self.mouse_device.set_evbit(EV_REL)
        self.mouse_device.set_keybit(BTN_LEFT)
        self.mouse_device.set_keybit(BTN_RIGHT)
        self.mouse_device.set_keybit(BTN_MIDDLE)
        self.mouse_device.set_relbit(REL_X)
        self.mouse_device.set_relbit(REL_Y)
        self.mouse_device.set_relbit(REL_WHEEL)

        self.keyboard_device = UInputDevice("Aion Virtual Keyboard")
        self.keyboard_device.open()
        self.keyboard_device.set_evbit(EV_KEY)
        for k in [KEY_W, KEY_A, KEY_S, KEY_D, KEY_SPACE, KEY_LEFTSHIFT,
                   KEY_LEFTCTRL, KEY_TAB, KEY_R, KEY_E, KEY_F, KEY_Q,
                   KEY_J, KEY_K, KEY_L, KEY_SEMICOLON]:
            self.keyboard_device.set_keybit(k)

        self.gamepad_device = UInputDevice("Aion Virtual Gamepad", bustype=0x05)
        self.gamepad_device.open()
        self.gamepad_device.set_evbit(EV_KEY)
        self.gamepad_device.set_evbit(EV_ABS)
        self.gamepad_device.set_keybit(BTN_LEFT)
        self.gamepad_device.set_keybit(BTN_RIGHT)
        self.gamepad_device.set_keybit(BTN_MIDDLE)
        self.gamepad_device.set_absbit(ABS_X, 32767)
        self.gamepad_device.set_absbit(ABS_Y, 32767)
        self.gamepad_device.set_absbit(ABS_RX, 32767)
        self.gamepad_device.set_absbit(ABS_RY, 32767)
        logger.info("Virtual input devices created")

    def _detect_active_window(self):
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", "Waydroid"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                wid = result.stdout.strip().split("\n")[0]
                name_result = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=5,
                )
                if name_result.returncode == 0:
                    return name_result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        except Exception as e:
            logger.debug("Window detection failed: %s", e)
        return None

    def _extract_package_from_title(self, title):
        if not title:
            return ""
        parts = title.split(" - ")
        if len(parts) > 1:
            candidate = parts[-1].strip()
            if "." in candidate and all(c.isalnum() or c in "._" for c in candidate):
                return candidate
        if "." in title:
            for part in title.replace("/", ".").split():
                if "." in part and len(part) > 5:
                    cleaned = part.strip("[](){}")
                    if "." in cleaned:
                        return cleaned
        return ""

    def _match_genre_template(self, package_name):
        pkg = package_name.lower()
        kw_map = {
            "fps": ["fps", "shooter", "gun", "war", "battle", "combat", "strike", "duty", "sniper", "zombie"],
            "moba": ["moba", "arena", "league", "legends", "clash", "brawl", "heroes"],
            "racing": ["racing", "car", "drive", "speed", "racer", "drift", "asphalt"],
            "rpg": ["rpg", "quest", "dungeon", "fantasy", "legend", "chronicle", "adventure"],
        }
        for genre, keywords in kw_map.items():
            for kw in keywords:
                if kw in pkg:
                    return genre
        return None

    def _get_profile_for_package(self, package_name):
        if package_name in self.profiles:
            return self.profiles[package_name]
        genre = self._match_genre_template(package_name)
        if genre and genre in self.profiles:
            template = self.profiles[genre]
            profile = KeyMappingProfile(
                name=f"{template.name} (auto)",
                package=package_name,
                description=f"Auto-generated from {genre} template",
                screen_width=template.screen_width,
                screen_height=template.screen_height,
                zones=list(template.zones),
                key_bindings=dict(template.key_bindings),
                touch_to_mouse=template.touch_to_mouse,
                left_stick_zone=template.left_stick_zone,
                right_stick_zone=template.right_stick_zone,
            )
            self.profiles[package_name] = profile
            return profile
        if "fps" in self.profiles:
            return self.profiles["fps"]
        return KeyMappingProfile(name="generic", package=package_name)

    def _resolve_binding(self, binding_str):
        key_map = {
            "KEY_W": KEY_W, "KEY_A": KEY_A, "KEY_S": KEY_S, "KEY_D": KEY_D,
            "KEY_SPACE": KEY_SPACE, "KEY_LEFTSHIFT": KEY_LEFTSHIFT,
            "KEY_LEFTCTRL": KEY_LEFTCTRL, "KEY_TAB": KEY_TAB,
            "KEY_R": KEY_R, "KEY_E": KEY_E, "KEY_F": KEY_F, "KEY_Q": KEY_Q,
            "KEY_J": KEY_J, "KEY_K": KEY_K, "KEY_L": KEY_L,
            "KEY_SEMICOLON": KEY_SEMICOLON,
            "BTN_LEFT": BTN_LEFT, "BTN_RIGHT": BTN_RIGHT,
        }
        return key_map.get(binding_str)

    def _in_zone(self, nx, ny, zone):
        return zone.x_min <= nx <= zone.x_max and zone.y_min <= ny <= zone.y_max

    def _handle_left_stick(self, nx, ny, zone):
        cx = (zone.x_min + zone.x_max) / 2.0
        cy = (zone.y_min + zone.y_max) / 2.0
        dx = (nx - cx) * 2.0 * zone.sensitivity
        dy = (ny - cy) * 2.0 * zone.sensitivity
        dz = zone.deadzone
        if abs(dx) < dz:
            dx = 0.0
        if abs(dy) < dz:
            dy = 0.0
        dx = max(-1.0, min(1.0, dx))
        dy = max(-1.0, min(1.0, dy))
        axis_x = int(dx * 16383 + 16383)
        axis_y = int(dy * 16383 + 16383)
        self.gamepad_device.emit_event(EV_ABS, ABS_X, axis_x)
        self.gamepad_device.emit_event(EV_ABS, ABS_Y, axis_y)
        self.gamepad_device.syn()

    def _handle_right_stick(self, nx, ny, zone):
        cx = (zone.x_min + zone.x_max) / 2.0
        cy = (zone.y_min + zone.y_max) / 2.0
        dx = (nx - cx) * 2.0 * zone.sensitivity
        dy = (ny - cy) * 2.0 * zone.sensitivity
        dz = zone.deadzone
        if abs(dx) < dz:
            dx = 0.0
        if abs(dy) < dz:
            dy = 0.0
        rel_x = int(dx * 8)
        rel_y = int(dy * 8)
        self.mouse_device.emit_event(EV_REL, REL_X, rel_x)
        self.mouse_device.emit_event(EV_REL, REL_Y, rel_y)
        self.mouse_device.syn()

    def _handle_tilt_steer(self, nx, ny, zone):
        cx = (zone.x_min + zone.x_max) / 2.0
        dx = (nx - cx) * 2.0 * zone.sensitivity
        dz = zone.deadzone
        if abs(dx) < dz:
            dx = 0.0
        dx = max(-1.0, min(1.0, dx))
        axis_x = int(dx * 16383 + 16383)
        self.gamepad_device.emit_event(EV_ABS, ABS_X, axis_x)
        self.gamepad_device.syn()

    def _handle_swipe(self, tp):
        if abs(tp.swipe_dx) > self._swipe_threshold:
            direction = -1 if tp.swipe_dx > 0 else 1
            self.mouse_device.emit_event(EV_REL, REL_WHEEL, direction)
            self.mouse_device.syn()

    def _process_touch(self, slot, x, y, pressure, is_down):
        with self.lock:
            if slot not in self.touch_points:
                self.touch_points[slot] = TouchPoint(slot=slot)
            tp = self.touch_points[slot]
            if is_down and not tp.is_down:
                tp.tracking_id = int(time.time() * 1000) % 32767
                tp.is_down = True
                tp.start_x = x
                tp.start_y = y
                tp.swipe_dx = 0.0
                tp.swipe_dy = 0.0
            elif not is_down and tp.is_down:
                tp.is_down = False
                tp.tracking_id = -1
            tp.x = x / 1280.0
            tp.y = y / 720.0
            tp.pressure = pressure / 4095.0 if pressure > 0 else 0.0
            tp.swipe_dx = x - tp.start_x
            tp.swipe_dy = y - tp.start_y
            if not self.current_profile:
                return
            profile = self.current_profile
            nx, ny = tp.x, tp.y
            if is_down:
                if profile.left_stick_zone and self._in_zone(nx, ny, profile.left_stick_zone):
                    self._handle_left_stick(nx, ny, profile.left_stick_zone)
                elif profile.right_stick_zone and self._in_zone(nx, ny, profile.right_stick_zone):
                    self._handle_right_stick(nx, ny, profile.right_stick_zone)
                else:
                    for zone in profile.zones:
                        if zone.action == "tap" and self._in_zone(nx, ny, zone):
                            code = self._resolve_binding(zone.binding)
                            if code is not None:
                                self.keyboard_device.emit_event(EV_KEY, code, 1)
                                self.keyboard_device.syn()
                                time.sleep(0.05)
                                self.keyboard_device.emit_event(EV_KEY, code, 0)
                                self.keyboard_device.syn()
                            elif zone.binding == "right_click":
                                self.mouse_device.emit_event(EV_KEY, BTN_RIGHT, 1)
                                self.mouse_device.syn()
                                time.sleep(0.05)
                                self.mouse_device.emit_event(EV_KEY, BTN_RIGHT, 0)
                                self.mouse_device.syn()
                            break
                        elif zone.action == "tilt_steer" and self._in_zone(nx, ny, zone):
                            self._handle_tilt_steer(nx, ny, zone)
                            break
            if not is_down and (abs(tp.swipe_dx) > self._swipe_threshold or abs(tp.swipe_dy) > self._swipe_threshold):
                self._handle_swipe(tp)

    def _monitor_loop(self):
        logger.info("Monitor loop started")
        last_package = ""
        poll_interval = 2.0
        while self.running:
            try:
                window_title = self._detect_active_window()
                if window_title:
                    pkg = self._extract_package_from_title(window_title)
                    if pkg and pkg != last_package:
                        profile = self._get_profile_for_package(pkg)
                        self.current_profile = profile
                        self.current_package = pkg
                        last_package = pkg
                        logger.info("Active profile: %s for %s", profile.name, pkg)
                    elif not pkg and last_package:
                        self.current_profile = None
                        self.current_package = ""
                        last_package = ""
                        logger.info("No active Waydroid window, clearing profile")
                else:
                    if last_package:
                        self.current_profile = None
                        self.current_package = ""
                        last_package = ""
            except Exception as e:
                logger.error("Monitor error: %s", e)
            time.sleep(poll_interval)

    def _simulate_touch(self, x, y, slot=0, is_down=True):
        pressure = 2048 if is_down else 0
        self._process_touch(slot, x, y, pressure, is_down)

    def _simulate_swipe(self, x1, y1, x2, y2, steps=10, slot=0, delay=0.01):
        self._simulate_touch(x1, y1, slot, is_down=True)
        time.sleep(delay)
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(x1 + (x2 - x1) * t)
            cy = int(y1 + (y2 - y1) * t)
            self._process_touch(slot, cx, cy, 2048, True)
            time.sleep(delay)
        self._simulate_touch(x2, y2, slot, is_down=False)

    def start(self):
        self.running = True
        self._load_profiles()
        self._setup_devices()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("Key mapper daemon started")

    def stop(self):
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        if self.mouse_device:
            self.mouse_device.close()
        if self.keyboard_device:
            self.keyboard_device.close()
        if self.gamepad_device:
            self.gamepad_device.close()
        logger.info("Key mapper daemon stopped")

    def run(self):
        self.start()

        def handle_signal(signum, frame):
            logger.info("Received signal %d, shutting down", signum)
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aion Key Mapper Daemon")
    parser.add_argument("--simulate-touch", nargs=4, type=int,
                        metavar=("X", "Y", "SLOT", "DOWN"),
                        help="Simulate a touch event (for testing)")
    parser.add_argument("--simulate-swipe", nargs=6, type=int,
                        metavar=("X1", "Y1", "X2", "Y2", "STEPS", "SLOT"),
                        help="Simulate a swipe gesture (for testing)")
    args = parser.parse_args()

    daemon = KeyMapperDaemon()

    if args.simulate_touch:
        x, y, slot, down = args.simulate_touch
        daemon._load_profiles()
        daemon._process_touch(slot, x, y, 2048 if down else 0, bool(down))
        return

    if args.simulate_swipe:
        x1, y1, x2, y2, steps, slot = args.simulate_swipe
        daemon._load_profiles()
        daemon._simulate_swipe(x1, y1, x2, y2, steps, slot)
        return

    daemon.run()


if __name__ == "__main__":
    main()
