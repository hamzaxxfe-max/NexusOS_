#!/usr/bin/env python3
import os
import sys
import json
import signal
import time
import asyncio
import logging
import math
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any

import evdev
from evdev import UInput, ecodes, AbsInfo

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "input-engine.log"
CONFIG_PATH = Path("/etc/aion/input-mapping.json")

logger = logging.getLogger("aion-input-engine")

DEFAULT_CONFIG = {
    "deadzone": 0.15,
    "mouse_sensitivity": 3.0,
    "mouse_acceleration": 1.5,
    "mouse_max_speed": 25,
    "scroll_sensitivity": 3,
    "wasd_threshold": 0.5,
    "wasd_repeat_ms": 16,
    "gamepad_enabled": True,
    "analog_to_digital_threshold": 0.5,
    "hotplug_scan_interval": 5,
    "button_mappings": {
        "xbox": {
            "BTN_SOUTH": "BTN_LEFT",
            "BTN_EAST": "BTN_RIGHT",
            "BTN_NORTH": "KEY_F11",
            "BTN_WEST": "KEY_F12",
            "BTN_TL": "KEY_LEFTALT",
            "BTN_TR": "KEY_TAB",
            "BTN_SELECT": "KEY_TAB",
            "BTN_START": "KEY_LEFTMETA",
            "BTN_THUMBL": None,
            "BTN_THUMBR": None,
            "ABS_HAT0X_RIGHT": "KEY_D",
            "ABS_HAT0X_LEFT": "KEY_A",
            "ABS_HAT0Y_UP": "KEY_W",
            "ABS_HAT0Y_DOWN": "KEY_S",
            "ABS_RX": "mouse_x",
            "ABS_RY": "mouse_y",
            "ABS_X": "stick_left_x",
            "ABS_Y": "stick_left_y"
        },
        "playstation": {
            "BTN_SOUTH": "BTN_LEFT",
            "BTN_EAST": "BTN_RIGHT",
            "BTN_NORTH": "KEY_F11",
            "BTN_WEST": "KEY_F12",
            "BTN_TL": "KEY_LEFTALT",
            "BTN_TR": "KEY_TAB",
            "BTN_SELECT": "KEY_TAB",
            "BTN_START": "KEY_LEFTMETA",
            "BTN_THUMBL": None,
            "BTN_THUMBR": None,
            "ABS_HAT0X_RIGHT": "KEY_D",
            "ABS_HAT0X_LEFT": "KEY_A",
            "ABS_HAT0Y_UP": "KEY_W",
            "ABS_HAT0Y_DOWN": "KEY_S",
            "ABS_RX": "mouse_x",
            "ABS_RY": "mouse_y",
            "ABS_X": "stick_left_x",
            "ABS_Y": "stick_left_y"
        },
        "generic": {
            "BTN_SOUTH": "BTN_LEFT",
            "BTN_EAST": "BTN_RIGHT",
            "BTN_NORTH": "KEY_F11",
            "BTN_WEST": "KEY_F12",
            "BTN_TL": "KEY_LEFTALT",
            "BTN_TR": "KEY_TAB",
            "BTN_SELECT": "KEY_TAB",
            "BTN_START": "KEY_LEFTMETA",
            "BTN_THUMBL": None,
            "BTN_THUMBR": None,
            "ABS_HAT0X_RIGHT": "KEY_D",
            "ABS_HAT0X_LEFT": "KEY_A",
            "ABS_HAT0Y_UP": "KEY_W",
            "ABS_HAT0Y_DOWN": "KEY_S",
            "ABS_RX": "mouse_x",
            "ABS_RY": "mouse_y",
            "ABS_X": "stick_left_x",
            "ABS_Y": "stick_left_y"
        }
    }
}


class InputConfig:
    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path
        self.data: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        try:
            if self.config_path.exists():
                with open(self.config_path, "r") as f:
                    loaded = json.load(f)
                for key in DEFAULT_CONFIG:
                    if key in loaded:
                        if isinstance(DEFAULT_CONFIG[key], dict):
                            self.data[key] = {**DEFAULT_CONFIG[key], **loaded[key]}
                        else:
                            self.data[key] = loaded[key]
                logger.info("Loaded input config from %s", self.config_path)
            else:
                logger.warning("Config not found at %s, using defaults", self.config_path)
        except Exception as e:
            logger.error("Failed to load config: %s, using defaults", e)
            self.data = dict(DEFAULT_CONFIG)

    @property
    def deadzone(self) -> float:
        return float(self.data.get("deadzone", 0.15))

    @property
    def mouse_sensitivity(self) -> float:
        return float(self.data.get("mouse_sensitivity", 3.0))

    @property
    def mouse_acceleration(self) -> float:
        return float(self.data.get("mouse_acceleration", 1.5))

    @property
    def mouse_max_speed(self) -> float:
        return float(self.data.get("mouse_max_speed", 25))

    @property
    def scroll_sensitivity(self) -> int:
        return int(self.data.get("scroll_sensitivity", 3))

    @property
    def wasd_threshold(self) -> float:
        return float(self.data.get("wasd_threshold", 0.5))

    @property
    def wasd_repeat_ms(self) -> int:
        return int(self.data.get("wasd_repeat_ms", 16))

    @property
    def hotplug_scan_interval(self) -> int:
        return int(self.data.get("hotplug_scan_interval", 5))

    @property
    def gamepad_enabled(self) -> bool:
        return bool(self.data.get("gamepad_enabled", True))

    def get_button_mapping(self, gamepad_type: str) -> Dict[str, Optional[str]]:
        mappings = self.data.get("button_mappings", DEFAULT_CONFIG["button_mappings"])
        return mappings.get(gamepad_type, mappings.get("generic", {}))


class GamepadDevice:
    GAMEPAD_BUTTONS = {
        ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH, ecodes.BTN_WEST,
        ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_TL2, ecodes.BTN_TR2,
        ecodes.BTN_SELECT, ecodes.BTN_START, ecodes.BTN_MODE,
        ecodes.BTN_THUMBL, ecodes.BTN_THUMBR, ecodes.BTN_GAMEPAD,
    }
    GAMEPAD_AXES = {
        ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_Z,
        ecodes.ABS_RX, ecodes.ABS_RY, ecodes.ABS_RZ,
        ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y,
    }

    def __init__(self, device: evdev.InputDevice, gamepad_type: str = "generic"):
        self.device = device
        self.path = device.path
        self.name = device.name
        self.gamepad_type = gamepad_type
        self.capabilities = device.capabilities()
        self.abs_ranges: Dict[int, AbsInfo] = {}
        if ecodes.EV_ABS in self.capabilities:
            for abs_info in self.capabilities[ecodes.EV_ABS]:
                if isinstance(abs_info, tuple):
                    code, info = abs_info
                    self.abs_ranges[code] = info
        self.left_stick = [0.0, 0.0]
        self.right_stick = [0.0, 0.0]
        self.pressed_buttons: Set[int] = set()
        self.hat_state = [0, 0]

    def is_gamepad(self) -> bool:
        has_gamepad_button = ecodes.EV_KEY in self.capabilities and any(
            btn in self.capabilities[ecodes.EV_KEY]
            for btn in self.GAMEPAD_BUTTONS
        )
        has_axes = ecodes.EV_ABS in self.capabilities and any(
            ax in [abs_item[0] if isinstance(abs_item, tuple) else abs_item
                   for abs_item in self.capabilities.get(ecodes.EV_ABS, [])]
            for ax in self.GAMEPAD_AXES
        )
        return has_gamepad_button and has_axes

    def detect_type(self) -> str:
        name_lower = self.name.lower()
        if any(kw in name_lower for kw in ["xbox", "x-input", "xinput", "microsoft"]):
            return "xbox"
        elif any(kw in name_lower for kw in ["playstation", "ps4", "ps5", "dualshock", "dualsense", "sony"]):
            return "playstation"
        elif any(kw in name_lower for kw in ["nintendo", "switch", "pro controller"]):
            return "xbox"
        return "generic"

    def normalize_axis(self, code: int, value: int) -> float:
        if code in self.abs_ranges:
            info = self.abs_ranges[code]
            if info.max != info.min:
                normalized = (2.0 * (value - info.min) / (info.max - info.min)) - 1.0
                return max(-1.0, min(1.0, normalized))
        return 0.0

    def is_hat(self, code: int) -> bool:
        return code in (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y)


class UInputManager:
    MOUSE_REL_EVENTS = [
        (ecodes.EV_REL, ecodes.REL_X),
        (ecodes.EV_REL, ecodes.REL_Y),
        (ecodes.EV_REL, ecodes.REL_WHEEL),
    ]
    MOUSE_KEY_EVENTS = [
        ecodes.BTN_LEFT,
        ecodes.BTN_RIGHT,
        ecodes.BTN_MIDDLE,
    ]
    KEY_EVENTS = [
        ecodes.KEY_W, ecodes.KEY_A, ecodes.KEY_S, ecodes.KEY_D,
        ecodes.KEY_LEFTMETA, ecodes.KEY_TAB, ecodes.KEY_LEFTALT,
        ecodes.KEY_F11, ecodes.KEY_F12,
    ]

    def __init__(self):
        cap = {
            ecodes.EV_REL: [
                (ecodes.REL_X, AbsInfo(value=0, min=-1000, max=1000, fuzz=0, flat=0, resolution=0)),
                (ecodes.REL_Y, AbsInfo(value=0, min=-1000, max=1000, fuzz=0, flat=0, resolution=0)),
                (ecodes.REL_WHEEL, AbsInfo(value=0, min=-1, max=1, fuzz=0, flat=0, resolution=0)),
            ],
            ecodes.EV_KEY: self.MOUSE_KEY_EVENTS + self.KEY_EVENTS,
        }
        self.uinput = UInput(cap, name="aion-virtual-input", phys="aion/input0")
        self.key_states: Dict[int, bool] = {}

    def emit_relative(self, axis: int, value: int) -> None:
        self.uinput.write(ecodes.EV_REL, axis, value)
        self.uinput.syn()

    def emit_key(self, key_code: int, pressed: bool) -> None:
        current = self.key_states.get(key_code, False)
        if current != pressed:
            self.uinput.write(ecodes.EV_KEY, key_code, 1 if pressed else 0)
            self.uinput.syn()
            self.key_states[key_code] = pressed

    def release_all(self) -> None:
        for key_code, pressed in list(self.key_states.items()):
            if pressed:
                self.uinput.write(ecodes.EV_KEY, key_code, 0)
        self.uinput.syn()
        self.key_states.clear()

    def destroy(self) -> None:
        self.release_all()
        self.uinput.close()


class StickProcessor:
    def __init__(self, deadzone: float, sensitivity: float, acceleration: float, max_speed: float):
        self.deadzone = deadzone
        self.sensitivity = sensitivity
        self.acceleration = acceleration
        self.max_speed = max_speed

    def apply_deadzone(self, x: float, y: float) -> Tuple[float, float]:
        magnitude = math.sqrt(x * x + y * y)
        if magnitude < self.deadzone:
            return (0.0, 0.0)
        scaled_magnitude = (magnitude - self.deadzone) / (1.0 - self.deadzone)
        scaled_magnitude = min(scaled_magnitude, 1.0)
        if magnitude > 0:
            norm_x = x / magnitude
            norm_y = y / magnitude
        else:
            norm_x = 0.0
            norm_y = 0.0
        return (norm_x * scaled_magnitude, norm_y * scaled_magnitude)

    def compute_mouse_delta(self, x: float, y: float) -> Tuple[int, int]:
        nx, ny = self.apply_deadzone(x, y)
        if nx == 0.0 and ny == 0.0:
            return (0, 0)
        magnitude = math.sqrt(nx * nx + ny * ny)
        accelerated = math.pow(magnitude, self.acceleration) * self.sensitivity
        accelerated = min(accelerated, self.max_speed)
        if magnitude > 0:
            dx = nx / magnitude * accelerated
            dy = ny / magnitude * accelerated
        else:
            dx = 0.0
            dy = 0.0
        return (int(round(dx)), int(round(dy)))

    def compute_wasd(self, x: float, y: float, threshold: float) -> Dict[str, bool]:
        nx, ny = self.apply_deadzone(x, y)
        return {
            "w": ny < -threshold,
            "s": ny > threshold,
            "a": nx < -threshold,
            "d": nx > threshold,
        }


class WASDManager:
    def __init__(self, uinput_mgr: UInputManager, threshold: float, repeat_ms: int):
        self.uinput_mgr = uinput_mgr
        self.threshold = threshold
        self.repeat_ms = repeat_ms
        self.key_map = {
            "w": ecodes.KEY_W,
            "a": ecodes.KEY_A,
            "s": ecodes.KEY_S,
            "d": ecodes.KEY_D,
        }
        self.current_state = {"w": False, "a": False, "s": False, "d": False}

    def update(self, wasd_state: Dict[str, bool]) -> None:
        for key, pressed in wasd_state.items():
            if key in self.key_map:
                current = self.current_state.get(key, False)
                if current != pressed:
                    self.uinput_mgr.emit_key(self.key_map[key], pressed)
                    self.current_state[key] = pressed

    def release_all(self) -> None:
        for key, pressed in list(self.current_state.items()):
            if pressed and key in self.key_map:
                self.uinput_mgr.emit_key(self.key_map[key], False)
                self.current_state[key] = False


class ScrollManager:
    def __init__(self, uinput_mgr: UInputManager, sensitivity: int):
        self.uinput_mgr = uinput_mgr
        self.sensitivity = sensitivity
        self.accumulated = 0.0

    def process_dpad(self, hat_y: float) -> None:
        if hat_y < -0.5:
            self.uinput_mgr.emit_relative(ecodes.REL_WHEEL, self.sensitivity)
        elif hat_y > 0.5:
            self.uinput_mgr.emit_relative(ecodes.REL_WHEEL, -self.sensitivity)

    def process_stick(self, y: float, deadzone: float) -> None:
        if abs(y) > deadzone:
            self.accumulated += y * 0.1
            while self.accumulated >= 1.0:
                self.uinput_mgr.emit_relative(ecodes.REL_WHEEL, 1)
                self.accumulated -= 1.0
            while self.accumulated <= -1.0:
                self.uinput_mgr.emit_relative(ecodes.REL_WHEEL, -1)
                self.accumulated += 1.0
        else:
            self.accumulated = 0.0


class ButtonMapper:
    def __init__(self, uinput_mgr: UInputManager, mapping: Dict[str, Optional[str]]):
        self.uinput_mgr = uinput_mgr
        self.mapping = mapping
        self.key_code_map = self._build_key_code_map()

    def _build_key_code_map(self) -> Dict[str, int]:
        code_map = {}
        for attr_name in dir(ecodes):
            if attr_name.startswith("KEY_") or attr_name.startswith("BTN_"):
                code_map[attr_name] = getattr(ecodes, attr_name)
        code_map["mouse_x"] = -1
        code_map["mouse_y"] = -2
        code_map["stick_left_x"] = -3
        code_map["stick_left_y"] = -4
        return code_map

    def resolve_action(self, evdev_code_name: str) -> Optional[str]:
        return self.mapping.get(evdev_code_name)

    def get_key_code(self, action: str) -> Optional[int]:
        return self.key_code_map.get(action)


class InputEngineDaemon:
    def __init__(self):
        self.config = InputConfig()
        self.uinput_mgr: Optional[UInputManager] = None
        self.gamepads: Dict[str, GamepadDevice] = {}
        self.known_devices: Set[str] = set()
        self.running = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.wasd_managers: Dict[str, WASDManager] = {}
        self.scroll_managers: Dict[str, ScrollManager] = {}
        self.button_mappers: Dict[str, ButtonMapper] = {}
        self.stick_processors: Dict[str, StickProcessor] = {}
        self._setup_logging()
        self._setup_signal_handlers()

    def _setup_logging(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.setLevel(logging.DEBUG)

    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame: Any) -> None:
        logger.info("Received signal %d, shutting down", signum)
        self.running = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _init_uinput(self) -> None:
        self.uinput_mgr = UInputManager()
        logger.info("Virtual input device created: %s", self.uinput_mgr.uinput.name)

    def _detect_gamepads(self) -> List[str]:
        new_paths = []
        try:
            devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        except (OSError, PermissionError) as e:
            logger.error("Failed to list input devices: %s", e)
            return new_paths
        current_paths = set()
        for device in devices:
            path = device.path
            current_paths.add(path)
            if path not in self.known_devices:
                try:
                    gamepad = GamepadDevice(device)
                    if gamepad.is_gamepad():
                        gamepad.gamepad_type = gamepad.detect_type()
                        self.gamepads[path] = gamepad
                        self.known_devices.add(path)
                        new_paths.append(path)
                        logger.info(
                            "Detected gamepad: %s (%s) at %s",
                            gamepad.name, gamepad.gamepad_type, path
                        )
                        self._setup_gamepad_handlers(path)
                except (OSError, ValueError) as e:
                    logger.warning("Failed to examine device %s: %s", path, e)
                finally:
                    try:
                        device.close()
                    except Exception:
                        pass
        removed = set(self.known_devices) - current_paths
        for path in removed:
            if path in self.gamepads:
                logger.info("Gamepad removed: %s", self.gamepads[path].name)
                del self.gamepads[path]
            self.known_devices.discard(path)
            self.wasd_managers.pop(path, None)
            self.scroll_managers.pop(path, None)
            self.button_mappers.pop(path, None)
            self.stick_processors.pop(path, None)
        return new_paths

    def _setup_gamepad_handlers(self, path: str) -> None:
        gamepad = self.gamepads.get(path)
        if not gamepad:
            return
        mapping = self.config.get_button_mapping(gamepad.gamepad_type)
        self.button_mappers[path] = ButtonMapper(self.uinput_mgr, mapping)
        self.stick_processors[path] = StickProcessor(
            deadzone=self.config.deadzone,
            sensitivity=self.config.mouse_sensitivity,
            acceleration=self.config.mouse_acceleration,
            max_speed=self.config.mouse_max_speed,
        )
        self.wasd_managers[path] = WASDManager(
            self.uinput_mgr,
            threshold=self.config.wasd_threshold,
            repeat_ms=self.config.wasd_repeat_ms,
        )
        self.scroll_managers[path] = ScrollManager(
            self.uinput_mgr,
            sensitivity=self.config.scroll_sensitivity,
        )

    async def _read_gamepad(self, path: str) -> None:
        gamepad = self.gamepads.get(path)
        if not gamepad:
            return
        device = gamepad.device
        try:
            async for event in device.async_read_loop():
                if not self.running:
                    break
                self._process_event(path, event)
        except (OSError, asyncio.CancelledError) as e:
            logger.debug("Gamepad read loop ended for %s: %s", path, e)
        finally:
            self.known_devices.discard(path)
            self.gamepads.pop(path, None)

    def _process_event(self, path: str, event: evdev.InputEvent) -> None:
        gamepad = self.gamepads.get(path)
        if not gamepad:
            return
        mapper = self.button_mappers.get(path)
        stick_proc = self.stick_processors.get(path)
        wasd_mgr = self.wasd_managers.get(path)
        scroll_mgr = self.scroll_managers.get(path)
        if not mapper or not stick_proc:
            return
        if event.type == ecodes.EV_ABS:
            code_name = ecodes.bytype[ecodes.EV_ABS].get(event.code, f"ABS_{event.code}")
            normalized = gamepad.normalize_axis(event.code, event.value)

            if event.code == ecodes.ABS_X:
                gamepad.left_stick[0] = normalized
                action = mapper.resolve_action("ABS_X")
                if action == "stick_left_x":
                    wasd_state = stick_proc.compute_wasd(
                        gamepad.left_stick[0], gamepad.left_stick[1],
                        self.config.wasd_threshold
                    )
                    if wasd_mgr:
                        wasd_mgr.update(wasd_state)

            elif event.code == ecodes.ABS_Y:
                gamepad.left_stick[1] = normalized
                action = mapper.resolve_action("ABS_Y")
                if action == "stick_left_y":
                    wasd_state = stick_proc.compute_wasd(
                        gamepad.left_stick[0], gamepad.left_stick[1],
                        self.config.wasd_threshold
                    )
                    if wasd_mgr:
                        wasd_mgr.update(wasd_state)

            elif event.code == ecodes.ABS_RX:
                gamepad.right_stick[0] = normalized
                dx, dy = stick_proc.compute_mouse_delta(normalized, gamepad.right_stick[1])
                if dx != 0:
                    self.uinput_mgr.emit_relative(ecodes.REL_X, dx)
                if dy != 0:
                    self.uinput_mgr.emit_relative(ecodes.REL_Y, dy)

            elif event.code == ecodes.ABS_RY:
                gamepad.right_stick[1] = normalized
                dx, dy = stick_proc.compute_mouse_delta(gamepad.right_stick[0], normalized)
                if dx != 0:
                    self.uinput_mgr.emit_relative(ecodes.REL_X, dx)
                if dy != 0:
                    self.uinput_mgr.emit_relative(ecodes.REL_Y, dy)

            elif event.code == ecodes.ABS_HAT0X:
                if normalized > 0.5:
                    gamepad.hat_state[0] = 1
                elif normalized < -0.5:
                    gamepad.hat_state[0] = -1
                else:
                    gamepad.hat_state[0] = 0
                self._update_hat_keys(path, gamepad)

            elif event.code == ecodes.ABS_HAT0Y:
                if normalized < -0.5:
                    gamepad.hat_state[1] = 1
                elif normalized > 0.5:
                    gamepad.hat_state[1] = -1
                else:
                    gamepad.hat_state[1] = 0
                if scroll_mgr:
                    scroll_mgr.process_dpad(-normalized)

        elif event.type == ecodes.EV_KEY:
            code_name = ecodes.bytype[ecodes.EV_KEY].get(event.code, f"BTN_{event.code}")
            pressed = event.value > 0
            action = mapper.resolve_action(code_name)
            if action and action not in ("mouse_x", "mouse_y", "stick_left_x", "stick_left_y"):
                key_code = mapper.get_key_code(action)
                if key_code and key_code > 0:
                    self.uinput_mgr.emit_key(key_code, pressed)
                    gamepad.pressed_buttons.discard(event.code)
                    if pressed:
                        gamepad.pressed_buttons.add(event.code)

    def _update_hat_keys(self, path: str, gamepad: GamepadDevice) -> None:
        mapper = self.button_mappers.get(path)
        if not mapper:
            return
        mapping = mapper.mapping
        hx, hy = gamepad.hat_state
        right_action = mapping.get("ABS_HAT0X_RIGHT")
        left_action = mapping.get("ABS_HAT0X_LEFT")
        up_action = mapping.get("ABS_HAT0Y_UP")
        down_action = mapping.get("ABS_HAT0Y_DOWN")
        if right_action:
            code = mapper.get_key_code(right_action)
            if code:
                self.uinput_mgr.emit_key(code, hx > 0)
        if left_action:
            code = mapper.get_key_code(left_action)
            if code:
                self.uinput_mgr.emit_key(code, hx < 0)
        if up_action:
            code = mapper.get_key_code(up_action)
            if code:
                self.uinput_mgr.emit_key(code, hy > 0)
        if down_action:
            code = mapper.get_key_code(down_action)
            if code:
                self.uinput_mgr.emit_key(code, hy < 0)

    async def _hotplug_scanner(self) -> None:
        while self.running:
            self._detect_gamepads()
            await asyncio.sleep(self.config.hotplug_scan_interval)

    async def _run(self) -> None:
        self.running = True
        self._init_uinput()
        self._detect_gamepads()
        logger.info("Initial gamepad count: %d", len(self.gamepads))

        tasks = []
        for path in list(self.gamepads.keys()):
            task = asyncio.ensure_future(self._read_gamepad(path))
            tasks.append(task)

        hotplug_task = asyncio.ensure_future(self._hotplug_scanner())
        tasks.append(hotplug_task)

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def run(self) -> None:
        logger.info("Aion Input Engine starting (PID %d)", os.getpid())
        logger.info(
            "Config: deadzone=%.2f, sensitivity=%.2f, acceleration=%.2f",
            self.config.deadzone, self.config.mouse_sensitivity, self.config.mouse_acceleration,
        )
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._run())
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        logger.info("Shutting down Input Engine")
        self.running = False
        for path, gamepad in self.gamepads.items():
            try:
                gamepad.device.close()
            except Exception:
                pass
        self.gamepads.clear()
        self.known_devices.clear()
        for wasd_mgr in self.wasd_managers.values():
            wasd_mgr.release_all()
        self.wasd_managers.clear()
        self.scroll_managers.clear()
        self.button_mappers.clear()
        self.stick_processors.clear()
        if self.uinput_mgr:
            self.uinput_mgr.destroy()
            self.uinput_mgr = None
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        logger.info("Input Engine shut down cleanly")


def check_root() -> None:
    if os.getuid() != 0:
        print("Error: aion-input-engine must run as root", file=sys.stderr)
        sys.exit(1)


def check_dependencies() -> None:
    missing = []
    try:
        import evdev
    except ImportError:
        missing.append("python3-evdev")
    if missing:
        print(f"Missing dependencies: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    check_root()
    check_dependencies()
    daemon = InputEngineDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
