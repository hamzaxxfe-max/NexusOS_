#!/usr/bin/env python3
"""
Aion Zero-Lag Recording — Record games with zero FPS impact.

Uses hardware encoders (NVENC, VA-API, AMF) and PipeWire screen
capture for zero-overhead game recording.

Usage:
    aion-zero-lag-record --game <name>         # Record a game
    aion-zero-lag-record --screen               # Record screen
    aion-zero-lag-record --replay               # Instant replay (last 30s)
    aion-zero-lag-record --config               # Show encoder info
    aion-zero-lag-record daemon                 # Run replay buffer daemon
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "zero-lag-record.log"
CONFIG_FILE = Path("/etc/aion/record-config.json")
REPLAY_DIR = Path("/var/lib/aion/replay-buffer")

logger = logging.getLogger("zero-lag-record")

# Recording presets
PRESETS = {
    "quality": {
        "encoder": "auto",
        "codec": "hevc",
        "bitrate": "20M",
        "fps": 60,
        "preset": "slow",
        "cq": "18",
    },
    "balanced": {
        "encoder": "auto",
        "codec": "hevc",
        "bitrate": "15M",
        "fps": 60,
        "preset": "medium",
        "cq": "23",
    },
    "performance": {
        "encoder": "auto",
        "codec": "h264",
        "bitrate": "10M",
        "fps": 60,
        "preset": "fast",
        "cq": "28",
    },
    "replay": {
        "encoder": "auto",
        "codec": "hevc",
        "bitrate": "20M",
        "fps": 60,
        "preset": "fast",
        "cq": "20",
        "duration": 30,
    },
}


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)


def detect_encoder() -> str:
    """Detect the best available hardware encoder."""
    encoders_to_check = [
        ("nvenc", ["ffmpeg", "-encoders"], "nvenc"),
        ("vaapi", ["ffmpeg", "-encoders"], "vaapi"),
        ("amf", ["ffmpeg", "-encoders"], "amf"),
        ("qsv", ["ffmpeg", "-encoders"], "qsv"),
    ]

    for name, cmd, pattern in encoders_to_check:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if pattern in result.stdout.lower():
                logger.info("Detected encoder: %s", name)
                return name
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    # Check PipeWire screen capture
    try:
        result = subprocess.run(
            ["pw-record", "--list-targets"],
            capture_output=True, text=True, timeout=5,
        )
        if "screen" in result.stdout.lower() or "monitor" in result.stdout.lower():
            logger.info("PipeWire screen capture available")
            return "pipewire"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    logger.warning("No hardware encoder found, using software encoding (x264)")
    return "software"


def get_encoder_args(encoder: str, preset: dict) -> list:
    """Build FFmpeg encoder arguments."""
    codec = preset.get("codec", "h264")
    bitrate = preset.get("bitrate", "15M")
    cq = preset.get("cq", "23")

    if encoder == "nvenc":
        if codec == "hevc":
            return [
                "-c:v", "hevc_nvenc",
                "-preset", preset.get("preset", "medium"),
                "-b:v", bitrate,
                "-rc", "constqp",
                "-qp", cq,
                "-spatial-aq", "1",
                "-temporal-aq", "1",
                "-zerolatency", "1",
                "-rc-lookahead", "32",
            ]
        else:
            return [
                "-c:v", "h264_nvenc",
                "-preset", preset.get("preset", "medium"),
                "-b:v", bitrate,
                "-rc", "constqp",
                "-qp", cq,
                "-zerolatency", "1",
                "-rc-lookahead", "32",
            ]

    elif encoder == "vaapi":
        return [
            "-vaapi_device", "/dev/dri/renderD128",
            "-vf", "format=nv12,hwupload",
            "-c:v", "hevc_vaapi" if codec == "hevc" else "h264_vaapi",
            "-b:v", bitrate,
            "-qp", cq,
        ]

    elif encoder == "amf":
        return [
            "-c:v", "hevc_amf" if codec == "hevc" else "h264_amf",
            "-preset", preset.get("preset", "balanced"),
            "-b:v", bitrate,
            "-qp_i", cq,
            "-qp_p", cq,
        ]

    else:
        # Software fallback
        if codec == "hevc":
            return [
                "-c:v", "libx265",
                "-preset", preset.get("preset", "medium"),
                "-b:v", bitrate,
                "-qp", cq,
                "-x265-params", "log-level=error",
            ]
        else:
            return [
                "-c:v", "libx264",
                "-preset", preset.get("preset", "fast"),
                "-b:v", bitrate,
                "-qp", cq,
            ]


def get_pipewire_source() -> Optional[str]:
    """Get PipeWire screen capture source."""
    try:
        result = subprocess.run(
            ["pw-record", "--list-targets"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.split("\n"):
            if "screen" in line.lower() or "monitor" in line.lower():
                # Extract source name
                parts = line.strip().split()
                if parts:
                    return parts[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def record_game(game_name: str, preset_name: str = "balanced",
                duration: Optional[int] = None, output: Optional[str] = None):
    """Record a game using hardware encoder."""
    preset = PRESETS.get(preset_name, PRESETS["balanced"])
    encoder = detect_encoder()

    if encoder == "pipewire":
        source = get_pipewire_source()
        if not source:
            logger.error("No PipeWire screen source found")
            return False

        input_args = ["-f", "pipewire", "-i", source]
    else:
        # Find game window
        input_args = ["-f", "x11grab", "-framerate", str(preset["fps"])]

        # Try to find game window geometry
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", game_name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                wid = result.stdout.strip().split("\n")[0]
                result = subprocess.run(
                    ["xdotool", "getwindowgeometry", wid],
                    capture_output=True, text=True, timeout=5,
                )
                # Parse geometry
                import re
                match = re.search(r"(\d+)x(\d+).*?(\d+),(\d+)", result.stdout)
                if match:
                    w, h, x, y = match.groups()
                    input_args.extend([
                        "-video_size", f"{w}x{h}",
                        "-offset_x", x,
                        "-offset_y", y,
                        "-i", ":0.0",
                    ])
                else:
                    input_args.extend(["-i", ":0.0"])
            else:
                input_args.extend(["-i", ":0.0"])
        except (subprocess.TimeoutExpired, FileNotFoundError):
            input_args.extend(["-i", ":0.0"])

    # Output path
    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path.home() / "Videos" / "Aion"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = str(output_dir / f"{game_name}_{timestamp}.mkv")

    # Build FFmpeg command
    encoder_args = get_encoder_args(encoder, preset)

    cmd = ["ffmpeg", "-y"] + input_args + [
        "-t", str(duration) if duration else "0",
        "-r", str(preset["fps"]),
        "-pix_fmt", "yuv420p",
    ] + encoder_args + [
        "-c:a", "copy",
        "-movflags", "+faststart",
        output,
    ]

    logger.info("Recording with %s encoder: %s", encoder, output)
    logger.debug("Command: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            logger.info("Recording saved: %s", output)
            return True
        else:
            logger.error("Recording failed: %s", result.stderr[:500])
            return False
    except subprocess.TimeoutExpired:
        logger.error("Recording timed out")
        return False


def start_replay_buffer(game_name: str, duration: int = 30):
    """Start a replay buffer daemon that keeps the last N seconds."""
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)

    preset = PRESETS["replay"]
    encoder = detect_encoder()
    source = get_pipewire_source() if encoder == "pipewire" else ":0.0"

    # Circular buffer using FFmpeg segment muxer
    output_pattern = str(REPLAY_DIR / f"replay_%03d.mkv")

    cmd = [
        "ffmpeg", "-y",
        "-f", "pipewire" if encoder == "pipewire" else "x11grab",
        "-i", source,
        "-t", str(duration),
        "-r", str(preset["fps"]),
        "-pix_fmt", "yuv420p",
    ] + get_encoder_args(encoder, preset) + [
        "-c:a", "copy",
        "-f", "segment",
        "-segment_time", str(duration),
        "-segment_format", "matroska",
        "-reset_timestamps", "1",
        output_pattern,
    ]

    logger.info("Starting replay buffer (%d seconds)", duration)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Save PID
    pid_file = REPLAY_DIR / "replay.pid"
    pid_file.write_text(str(proc.pid))

    return proc


def save_replay(game_name: str):
    """Save the current replay buffer to a permanent location."""
    # Find the latest replay segment
    segments = sorted(REPLAY_DIR.glob("replay_*.mkv"))
    if not segments:
        logger.error("No replay segments found")
        return False

    latest = segments[-1]
    output_dir = Path.home() / "Videos" / "Aion" / "Replays"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"replay_{game_name}_{timestamp}.mkv"

    shutil.copy2(str(latest), str(output))
    logger.info("Replay saved: %s", output)
    return True


def show_config():
    """Show encoder information."""
    encoder = detect_encoder()
    print(f"Detected encoder: {encoder.upper()}")
    print()

    # Check available encoders
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        encoders = []
        for line in result.stdout.split("\n"):
            if any(e in line.lower() for e in ["nvenc", "vaapi", "amf", "qsv", "libx26"]):
                encoders.append(line.strip())
        print("Available encoders:")
        for enc in encoders:
            print(f"  {enc}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("FFmpeg not found")

    print()
    print("Recording presets:")
    for name, preset in PRESETS.items():
        print(f"  {name}: {preset['codec']} @ {preset['bitrate']}, {preset['fps']}fps")


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Aion Zero-Lag Recording")
    parser.add_argument("--game", help="Game name to record")
    parser.add_argument("--screen", action="store_true", help="Record full screen")
    parser.add_argument("--replay", action="store_true", help="Save instant replay")
    parser.add_argument("--config", action="store_true", help="Show encoder info")
    parser.add_argument("--daemon", action="store_true", help="Run replay buffer daemon")
    parser.add_argument("--preset", default="balanced",
                       choices=["quality", "balanced", "performance", "replay"],
                       help="Recording preset")
    parser.add_argument("--duration", type=int, help="Recording duration (seconds)")
    parser.add_argument("--output", help="Output file path")

    args = parser.parse_args()

    if args.config:
        show_config()
    elif args.game:
        record_game(args.game, args.preset, args.duration, args.output)
    elif args.screen:
        record_game("screen", args.preset, args.duration, args.output)
    elif args.replay:
        save_replay("game")
    elif args.daemon:
        start_replay_buffer("game")
        signal.pause()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
