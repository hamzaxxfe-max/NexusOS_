#!/usr/bin/env python3
import os
import sys
import json
import time
import signal
import glob
import shutil
import hashlib
import logging
import logging.handlers
import subprocess
import threading
import collections
from pathlib import Path
from datetime import datetime

CONFIG_PATH = Path("/etc/nexusos/capture-config.json")
DEFAULT_CONFIG = {
    "enabled": True,
    "buffer_seconds": 30,
    "fps": 60,
    "resolution": "native",
    "encoder": "vaapi",
    "quality": "balanced",
    "save_path": "~/Videos/NexusOS-Capture",
    "trigger_combo": "Guide+RB",
    "trigger_hold_ms": 2000,
    "pause_wallpaper": True,
}
LOG_DIR = Path("/var/log/nexusos")
LOG_PATH = LOG_DIR / "game-capture.log"
SEGMENT_DIR = Path("/tmp/nexusos-capture")
VAAPI_DEVICE = "/dev/dri/renderD128"
WALLPAPER_PROCS = ["wallpaper-engine", "swww", "mpvpaper", "swaybg", "hyprpaper"]
GAME_PATTERNS = [
    "wine", "steam", "proton", "lutris", "gamescope", "mangohud",
    "gamemode", "dxvk", "vkd3d", "d3dadapter", "mono", "gecko",
    "starfield", "cyberpunk", "elden", "baldurs", " Hogwarts",
    "native", "heroic", "epicgames", "gog", "itch",
]
DESKTOP_PATTERNS = [
    "desktop", "file manager", "nautilus", "dolphin", "thunar",
    "nemo", "caja", "pcmanfm", "kde", "gnome-shell", "explorer",
    "finder", "system", "settings", "terminal", "console",
]

logger = logging.getLogger("game-capture")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=5
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(console)


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                user_cfg = json.load(f)
            config.update(user_cfg)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load config: {e}, using defaults")
    return config


def expand_path(p):
    return str(Path(p).expanduser())


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip(), r.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", 1


class CircularBuffer:
    def __init__(self, duration_sec=30, fps=60):
        self.duration = duration_sec
        self.fps = fps
        self.buffer_size = duration_sec * fps
        self.frames = collections.deque(maxlen=self.buffer_size)

    def add_frame(self, frame_data, timestamp):
        self.frames.append((frame_data, timestamp))

    def get_buffered_frames(self):
        return list(self.frames)

    def clear(self):
        self.frames.clear()

    def __len__(self):
        return len(self.frames)


class SegmentManager:
    def __init__(self, segment_dir, max_segments=30):
        self.segment_dir = Path(segment_dir)
        self.max_segments = max_segments
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def get_segments(self):
        with self._lock:
            segs = sorted(self.segment_dir.glob("seg_*.ts"))
            return list(segs)

    def cleanup_old(self):
        with self._lock:
            segs = sorted(self.segment_dir.glob("seg_*.ts"))
            if len(segs) > self.max_segments:
                for seg in segs[: len(segs) - self.max_segments]:
                    try:
                        seg.unlink()
                    except OSError:
                        pass

    def clear_all(self):
        with self._lock:
            for seg in self.segment_dir.glob("seg_*.ts"):
                try:
                    seg.unlink()
                except OSError:
                    pass

    def concat_segments(self, output_path, count=None):
        with self._lock:
            segs = sorted(self.segment_dir.glob("seg_*.ts"))
        if count:
            segs = segs[-count:]
        if not segs:
            logger.warning("No segments to concatenate")
            return False
        concat_file = self.segment_dir / "concat_list.txt"
        try:
            with open(concat_file, "w") as f:
                for seg in segs:
                    f.write(f"file '{seg}'\n")
            out_dir = Path(output_path).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            cmd = (
                f"ffmpeg -y -f concat -safe 0 -i '{concat_file}' "
                f"-c copy -movflags +faststart '{output_path}' 2>/dev/null"
            )
            _, rc = run_cmd(cmd, timeout=60)
            concat_file.unlink(missing_ok=True)
            if rc != 0:
                logger.error(f"ffmpeg concat failed with rc={rc}")
                return False
            logger.info(f"Capture saved: {output_path}")
            return True
        except OSError as e:
            logger.error(f"Concat failed: {e}")
            return False


class GameDetector:
    def __init__(self):
        self._active_game = False
        self._active_title = ""

    def get_active_window(self):
        out, rc = run_cmd("xdotool getactivewindow getwindowname 2>/dev/null", timeout=5)
        if rc != 0 or not out:
            return ""
        return out.strip()

    def is_game(self, title):
        if not title:
            return False
        title_lower = title.lower()
        for pattern in GAME_PATTERNS:
            if pattern.lower() in title_lower:
                return True
        return False

    def is_desktop(self, title):
        if not title:
            return True
        title_lower = title.lower()
        for pattern in DESKTOP_PATTERNS:
            if pattern.lower() in title_lower:
                return True
        return False

    def update(self):
        title = self.get_active_window()
        was_game = self._active_game
        self._active_title = title
        self._active_game = self.is_game(title) and not self.is_desktop(title)
        if self._active_game and not was_game:
            logger.info(f"Game detected: {title}")
        elif not self._active_game and was_game:
            logger.info(f"Game lost focus: {title}")
        return self._active_game

    @property
    def active_game(self):
        return self._active_game

    @property
    def active_title(self):
        return self._active_title


class WallpaperController:
    def __init__(self):
        self._paused = False
        self._pids = []

    def _find_wallpaper_pids(self):
        pids = []
        out, _ = run_cmd("ps aux 2>/dev/null")
        for line in out.split("\n"):
            for proc in WALLPAPER_PROCS:
                if proc in line.lower():
                    parts = line.split()
                    try:
                        pid = int(parts[1])
                        pids.append(pid)
                    except (IndexError, ValueError):
                        pass
        return pids

    def pause(self):
        if self._paused:
            return
        self._pids = self._find_wallpaper_pids()
        for pid in self._pids:
            out, rc = run_cmd(f"kill -STOP {pid} 2>/dev/null")
            if rc == 0:
                logger.info(f"Paused wallpaper PID {pid}")
        self._paused = True

    def resume(self):
        if not self._paused:
            return
        for pid in self._pids:
            out, rc = run_cmd(f"kill -CONT {pid} 2>/dev/null")
            if rc == 0:
                logger.info(f"Resumed wallpaper PID {pid}")
        self._paused = False
        self._pids = []

    @property
    def is_paused(self):
        return self._paused


class VAAPIValidator:
    @staticmethod
    def device_exists():
        return Path(VAAPI_DEVICE).exists()

    @staticmethod
    def has_h264():
        out, rc = run_cmd(
            f"vainfo --display drm --device {VAAPI_DEVICE} 2>&1", timeout=10
        )
        if rc != 0:
            return False, out
        has_h264 = "H264" in out or "h264" in out
        has_encode = "VAProfileH264Main" in out or "VAProfileH264" in out
        profiles = [l.strip() for l in out.split("\n") if "H264" in l]
        return has_h264 and has_encode, "\n".join(profiles)

    @staticmethod
    def validate():
        if not VAAPIValidator.device_exists():
            logger.warning(f"VAAPI device not found: {VAAPI_DEVICE}")
            return False
        ok, info = VAAPIValidator.has_h264()
        if ok:
            logger.info(f"VAAPI H264 encoding available:\n{info}")
            return True
        else:
            logger.warning(f"VAAPI H264 not available: {info}")
            return False


class CaptureEngine:
    def __init__(self, config):
        self.config = config
        self.segment_mgr = SegmentManager(SEGMENT_DIR, max_segments=config["buffer_seconds"])
        self.game_detector = GameDetector()
        self.wallpaper = WallpaperController()
        self._process = None
        self._recording = False
        self._mode = None
        self._lock = threading.Lock()
        self._save_path = expand_path(config["save_path"])
        self._encoder = config["encoder"]
        self._quality = config["quality"]
        self._fps = config["fps"]
        self._resolution = config["resolution"]
        self._trigger_held = False
        self._trigger_start = 0
        self._trigger_count = 0

    def _get_resolution(self):
        if self._resolution == "native":
            out, rc = run_cmd("xdpyinfo 2>/dev/null | grep dimensions")
            if rc == 0 and "x" in out:
                try:
                    dims = out.split()[1]
                    return dims.split("x")[0] + "x" + dims.split("x")[1].split()[0]
                except (IndexError, ValueError):
                    pass
        return self._resolution

    def _get_save_path(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{self._save_path}/{ts}.mp4"

    def _build_vaanagi_cmd(self, output_path):
        res = self._get_resolution()
        vaapi_device = VAAPI_DEVICE
        quality_map = {"low": "28", "balanced": "24", "high": "18", "ultra": "15"}
        qp = quality_map.get(self._quality, "24")
        cmd = (
            f"ffmpeg -y "
            f"-vaapi_device {vaapi_device} "
            f"-f x11grab -framerate {self._fps} -video_size {res} -i :0.0 "
            f"-vf 'format=nv12,hwupload' "
            f"-c:v h264_vaapi -qp {qp} -quality 30 "
            f"-f segment -segment_time 1 -reset_timestamps 1 "
            f"-segment_format mpegts {SEGMENT_DIR}/seg_%03d.ts"
        )
        return cmd

    def _build_software_cmd(self):
        res = self._get_resolution()
        quality_map = {"low": "28", "balanced": "23", "high": "18", "ultra": "15"}
        qp = quality_map.get(self._quality, "23")
        cmd = (
            f"ffmpeg -y "
            f"-f x11grab -framerate {self._fps} -video_size {res} -i :0.0 "
            f"-c:v libx264 -preset ultrafast -qp {qp} -tune zerolatency "
            f"-pix_fmt yuv420p "
            f"-f segment -segment_time 1 -reset_timestamps 1 "
            f"-segment_format mpegts {SEGMENT_DIR}/seg_%03d.ts"
        )
        return cmd

    def _start_segment_capture(self):
        with self._lock:
            if self._recording:
                return False
            self.segment_mgr.clear_all()
            if self._encoder == "vaapi" and VAAPIValidator.validate():
                cmd = self._build_vaanagi_cmd(None)
                logger.info(f"Starting VAAPI segment capture")
            else:
                cmd = self._build_software_cmd()
                logger.info("Starting software segment capture (VAAPI unavailable)")
                self._encoder = "software"
            SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                self._process = subprocess.Popen(
                    cmd, shell=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, preexec_fn=os.setsid,
                )
                self._recording = True
                self._segment_cleanup_thread = threading.Thread(
                    target=self._segment_cleanup_loop, daemon=True
                )
                self._segment_cleanup_thread.start()
                return True
            except OSError as e:
                logger.error(f"Failed to start ffmpeg: {e}")
                return False

    def _segment_cleanup_loop(self):
        while self._recording:
            self.segment_mgr.cleanup_old()
            time.sleep(2)

    def _stop_segment_capture(self):
        with self._lock:
            if not self._recording or not self._process:
                return
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
            except OSError as e:
                logger.error(f"Error stopping ffmpeg: {e}")
            self._process = None
            self._recording = False
            logger.info("Segment capture stopped")

    def save_instant_replay(self, count=None):
        if count is None:
            count = self.config["buffer_seconds"]
        output = self._get_save_path()
        logger.info(f"Saving instant replay ({count}s) to {output}")
        ok = self.segment_mgr.concat_segments(output, count=count)
        if ok:
            self._send_notification(f"Instant replay saved: {Path(output).name}")
        return ok

    def save_manual_record(self):
        output = self._get_save_path()
        logger.info(f"Saving manual recording to {output}")
        ok = self.segment_mgr.concat_segments(output)
        if ok:
            self._send_notification(f"Recording saved: {Path(output).name}")
        return ok

    def _send_notification(self, message):
        out, rc = run_cmd(
            f"notify-send -a 'NexusOS Game Capture' -i camera-video "
            f"-u normal -t 5000 'Game Capture' '{message}' 2>/dev/null"
        )
        if rc != 0:
            logger.info(f"Notification: {message}")

    def handle_trigger_press(self):
        now = time.time()
        hold_ms = self.config["trigger_hold_ms"] / 1000.0
        if not self._trigger_held:
            self._trigger_held = True
            self._trigger_start = now
            self._trigger_count += 1
            return
        elapsed = now - self._trigger_start
        if elapsed >= hold_ms and self._trigger_count == 1:
            self._trigger_count += 1
            self._on_long_press()
        elif elapsed < 0.15 and not self._recording:
            pass

    def handle_trigger_release(self):
        if self._trigger_held:
            elapsed = time.time() - self._trigger_start
            hold_ms = self.config["trigger_hold_ms"] / 1000.0
            self._trigger_held = False
            self._trigger_start = 0
            if elapsed < hold_ms and self._trigger_count == 1:
                self._on_short_press()
            self._trigger_count = 0

    def _on_long_press(self):
        logger.info("Long press detected: Instant replay save")
        if self._recording:
            self.save_instant_replay()
        else:
            self._start_segment_capture()
            time.sleep(2)
            self.save_instant_replay()

    def _on_short_press(self):
        logger.info("Short press detected: Toggle manual recording")
        if self._recording:
            self._stop_segment_capture()
            self.save_manual_record()
            if self.config["pause_wallpaper"]:
                self.wallpaper.resume()
        else:
            self._start_segment_capture()
            if self.config["pause_wallpaper"] and self.game_detector.active_game:
                self.wallpaper.pause()

    def trigger_instant_save(self):
        logger.info("SIGUSR1 received: triggering instant save")
        if self._recording:
            self.save_instant_replay()

    def cleanup(self):
        logger.info("Cleaning up capture engine")
        self._stop_segment_capture()
        if self.config["pause_wallpaper"]:
            self.wallpaper.resume()
        self.segment_mgr.clear_all()
        self._send_notification("Game capture daemon stopped")


class InputMonitor:
    EVIOCGBIT = 0x80084502
    EV_ABS = 3
    ABS_X = 0
    ABS_HAT0X = 16
    KEY_BTN_SOUTH = 304
    KEY_BTN_EAST = 305
    KEY_BTN_NORTH = 307
    KEY_BTN_WEST = 308
    KEY_BTN_TL = 310
    KEY_BTN_TR = 311
    KEY_BTN_SELECT = 314
    KEY_BTN_START = 315
    KEY_BTN_MODE = 316
    KEY_BTN_THUMBL = 317
    KEY_BTN_THUMBR = 318

    def __init__(self, on_short_press, on_long_press, on_release):
        self._on_short_press = on_short_press
        self._on_long_press = on_long_press
        self._on_release = on_release
        self._running = False
        self._thread = None
        self._guide_held = False
        self._rb_held = False
        self._hold_start = 0

    def find_controller(self):
        for evdev_path in sorted(glob.glob("/dev/input/event*")):
            try:
                with open(evdev_path, "rb") as f:
                    caps = bytearray(128)
                    fcntl.ioctl(f.fileno(), self.EVIOCGBIT, caps)
                    if caps[self.KEY_BTN_MODE // 8] & (1 << (self.KEY_BTN_MODE % 8)):
                        return evdev_path
            except (OSError, PermissionError):
                continue
        for evdev_path in sorted(glob.glob("/dev/input/event*")):
            try:
                with open(evdev_path, "rb") as f:
                    caps = bytearray(128)
                    fcntl.ioctl(f.fileno(), self.EVIOCGBIT, caps)
                    has_gamepad = (
                        caps[self.KEY_BTN_SOUTH // 8] & (1 << (self.KEY_BTN_SOUTH % 8))
                    )
                    if has_gamepad:
                        return evdev_path
            except (OSError, PermissionError):
                continue
        return None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self):
        import fcntl
        dev_path = self.find_controller()
        if not dev_path:
            logger.warning("No gamepad detected, input monitoring disabled")
            logger.info("Trigger combo will use polling fallback")
            self._polling_fallback()
            return
        logger.info(f"Monitoring controller: {dev_path}")
        try:
            with open(dev_path, "rb") as f:
                while self._running:
                    try:
                        event = f.read(24)
                        if not event or len(event) < 24:
                            continue
                        tv_sec, tv_usec, ev_type, ev_code, ev_value = struct.unpack(
                            "llhhi", event
                        )
                        self._process_event(ev_type, ev_code, ev_value)
                    except (struct.error, OSError):
                        continue
        except (FileNotFoundError, PermissionError) as e:
            logger.error(f"Controller disconnected: {e}")
            self._polling_fallback()

    def _polling_fallback(self):
        logger.info("Using xdotool polling for trigger combo detection")
        while self._running:
            time.sleep(0.1)

    def _process_event(self, ev_type, ev_code, ev_value):
        if ev_type != 1:
            return
        pressed = ev_value == 1
        released = ev_value == 0

        if ev_code == self.KEY_BTN_MODE:
            self._guide_held = pressed
        elif ev_code == self.KEY_BTN_TR:
            self._rb_held = pressed

        if pressed and self._guide_held and self._rb_held:
            self._hold_start = time.time()
            self._on_short_press()
        elif released and (ev_code in (self.KEY_BTN_MODE, self.KEY_BTN_TR)):
            if self._guide_held or self._rb_held:
                pass
            else:
                self._on_release()
                self._hold_start = 0
            if not self._guide_held and not self._rb_held:
                pass


import fcntl
import struct


class GameCaptureDaemon:
    def __init__(self):
        self.config = load_config()
        self.engine = CaptureEngine(self.config)
        self._running = False

    def setup_signals(self):
        def handle_sigterm(signum, frame):
            logger.info("SIGTERM received")
            self._running = False

        def handle_sigusr1(signum, frame):
            logger.info("SIGUSR1 received")
            self.engine.trigger_instant_save()

        def handle_sighup(signum, frame):
            logger.info("SIGHUP received, reloading config")
            self.config = load_config()
            self.engine.config = self.config

        signal.signal(signal.SIGTERM, handle_sigterm)
        signal.signal(signal.SIGINT, handle_sigterm)
        signal.signal(signal.SIGUSR1, handle_sigusr1)
        signal.signal(signal.SIGHUP, handle_sighup)

    def _check_vaacapi(self):
        if self.config["encoder"] == "vaapi":
            if VAAPIValidator.validate():
                logger.info("VAAPI hardware encoding available")
                return True
            else:
                logger.warning(
                    "VAAPI not available, falling back to software x264 encoding"
                )
                self.config["encoder"] = "software"
                self.engine._encoder = "software"
                return False
        return False

    def _ensure_save_dir(self):
        save_dir = Path(expand_path(self.config["save_path"]))
        save_dir.mkdir(parents=True, exist_ok=True)
        return save_dir

    def _start_auto_capture(self):
        if not self.config["enabled"]:
            logger.info("Capture is disabled in config")
            return
        logger.info("Starting automatic segment capture (instant replay mode)")
        self.engine._start_segment_capture()
        if self.config["pause_wallpaper"] and self.engine.game_detector.active_game:
            self.engine.wallpaper.pause()

    def _monitor_loop(self):
        logger.info("Starting capture monitor loop")
        self._start_auto_capture()
        game_check_interval = 2
        last_game_check = 0

        while self._running:
            now = time.time()
            if now - last_game_check >= game_check_interval:
                self.engine.game_detector.update()
                if self.config["pause_wallpaper"]:
                    if self.engine.game_detector.active_game and self.engine._recording:
                        if not self.engine.wallpaper.is_paused:
                            self.engine.wallpaper.pause()
                    elif not self.engine.game_detector.active_game:
                        if self.engine.wallpaper.is_paused:
                            self.engine.wallpaper.resume()
                last_game_check = now
            time.sleep(0.5)

    def run(self):
        setup_logging()
        logger.info("=" * 60)
        logger.info("NexusOS Game Capture Daemon v1.0 starting")
        logger.info(f"Config: {CONFIG_PATH}")
        logger.info(f"Encoder: {self.config['encoder']}")
        logger.info(f"Buffer: {self.config['buffer_seconds']}s @ {self.config['fps']}fps")
        logger.info(f"Save path: {expand_path(self.config['save_path'])}")

        self.setup_signals()
        self._running = True
        self._ensure_save_dir()
        self._check_vaacapi()

        input_monitor = InputMonitor(
            on_short_press=self.engine.handle_trigger_press,
            on_long_press=self.engine.handle_trigger_press,
            on_release=self.engine.handle_trigger_release,
        )
        input_monitor.start()

        try:
            self._monitor_loop()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self.engine.cleanup()
            input_monitor.stop()
            logger.info("Game capture daemon stopped")


def main():
    daemon = GameCaptureDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
