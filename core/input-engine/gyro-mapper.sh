#!/bin/bash
set -euo pipefail
# Aion Gyroscope to Mouse Mapper — Steam Deck / handheld gyro input

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG_FILE="/var/log/aion/gyro-mapper.log"
PID_FILE="/run/aion-gyro-mapper.pid"
CONFIG_FILE="/etc/aion/gyro-mapper.conf"

DEFAULT_SENSITIVITY="1.0"
DEFAULT_DEADZONE="0.05"
DEFAULT_SMOOTHING="0.8"

log_info()    { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")"

load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        . "$CONFIG_FILE"
    fi
    SENSITIVITY="${SENSITIVITY:-$DEFAULT_SENSITIVITY}"
    DEADZONE="${DEADZONE:-$DEFAULT_DEADZONE}"
    SMOOTHING="${SMOOTHING:-$DEFAULT_SMOOTHING}"
}

find_gyro_device() {
    local gyro_device=""
    local gyro_device_name=""

    for event_path in /dev/input/event*; do
        [ ! -r "$event_path" ] && continue
        local device_name
        device_name=$(cat "/sys/class/input/$(basename "$event_path")/device/name" 2>/dev/null || echo "")

        local has_abs_rx=false
        local has_abs_ry=false
        local has_abs_rz=false

        local capabilities
        capabilities=$(cat "/sys/class/input/$(basename "$event_path")/device/capabilities/abs" 2>/dev/null || echo "0")

        if [ -f "/sys/class/input/$(basename "$event_path")/device/capabilities/abs" ]; then
            local abs_bitmap
            abs_bitmap=$(od -A n -t x1 "/sys/class/input/$(basename "$event_path")/device/capabilities/abs" 2>/dev/null | tr -d ' \n' || echo "0000000000000000")
            local byte_idx
            local rx_bit=14
            local ry_bit=15
            local rz_bit=16
            local rx_byte=$((rx_bit / 2))
            local ry_byte=$((ry_bit / 2))
            local rz_byte=$((rz_bit / 2))
        fi

        if [ -f "/sys/class/input/$(basename "$event_path")/device/capabilities/relative" ]; then
            :
        fi

        for capability_file in /sys/class/input/$(basename "$event_path")/device/capabilities/*; do
            :
        done

        if [ -f "/sys/class/input/$(basename "$event_path")/uevent" ]; then
            if grep -q "ABS_RX" "/sys/class/input/$(basename "$event_path")/device/capabilities/abs" 2>/dev/null; then
                has_abs_rx=true
            fi
        fi

        if python3 -c "
import struct
try:
    with open('$event_path', 'rb') as f:
        data = f.read(24)
        # EV_ABS = 3, ABS_RX=4, ABS_RY=5, ABS_RZ=6
        # Check if device supports gyro axes by reading evdev capabilities
except:
    pass
" 2>/dev/null; then
            :
        fi

        if [ -n "$device_name" ]; then
            case "${device_name,,}" in
                *gyro*|*accelerometer*|*imu*|*motion*|*joycon*)
                    gyro_device="$event_path"
                    gyro_device_name="$device_name"
                    break
                    ;;
            esac
        fi
    done

    if [ -z "$gyro_device" ]; then
        log_warn "No gyroscope device found by name — trying Python evdev scan"
        gyro_device=$(python3 -c "
import glob
from evdev import InputDevice, ecodes
for path in sorted(glob.glob('/dev/input/event*')):
    try:
        dev = InputDevice(path)
        caps = dev.capabilities()
        if ecodes.EV_ABS in caps:
            abs_codes = [c[0] if isinstance(c, tuple) else c for c in caps[ecodes.EV_ABS]]
            if ecodes.ABS_RX in abs_codes and ecodes.ABS_RY in abs_codes and ecodes.ABS_RZ in abs_codes:
                print(path)
                break
    except (PermissionError, OSError):
        continue
" 2>/dev/null || echo "")
    fi

    if [ -z "$gyro_device" ]; then
        log_error "No gyroscope device found"
        return 1
    fi

    log_success "Gyroscope device: $gyro_device ($gyro_device_name)"
    echo "$gyro_device"
}

start_mapper() {
    load_config

    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log_warn "Gyro mapper already running (PID $(cat "$PID_FILE"))"
        return 0
    fi

    local gyro_device
    gyro_device=$(find_gyro_device) || return 1

    log_info "Starting gyro mapper: sensitivity=$SENSITIVITY, deadzone=$DEADZONE, smoothing=$SMOOTHING"

    python3 - "$gyro_device" "$SENSITIVITY" "$DEADZONE" "$SMOOTHING" <<'PYEOF' &
import sys
import time
import signal
import math

try:
    import evdev
    from evdev import UInput, ecodes
except ImportError:
    print("ERROR: python3-evdev not installed", file=sys.stderr)
    sys.exit(1)

gyro_path = sys.argv[1]
sensitivity = float(sys.argv[2])
deadzone = float(sys.argv[3])
smoothing = float(sys.argv[4])

running = True

def signal_handler(sig, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

try:
    gyro = evdev.InputDevice(gyro_path)
except (PermissionError, OSError) as e:
    print(f"ERROR: Cannot open {gyro_path}: {e}", file=sys.stderr)
    sys.exit(1)

virtual_mouse = UInput({
    ecodes.EV_REL: [
        (ecodes.REL_X, evdev.AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)),
        (ecodes.REL_Y, evdev.AbsInfo(value=0, min=0, max=255, fuzz=0, flat=0, resolution=0)),
    ],
}, name='aion-gyro-mouse')

last_x = 0.0
last_y = 0.0
last_time = time.monotonic()

try:
    while running:
        try:
            for event in gyro.read_loop():
                if not running:
                    break
                if event.type == ecodes.EV_ABS:
                    if event.code == ecodes.ABS_RX:
                        raw_x = (event.value - 32768) / 32768.0
                        if abs(raw_x) < deadzone:
                            raw_x = 0.0
                    elif event.code == ecodes.ABS_RY:
                        raw_y = (event.value - 32768) / 32768.0
                        if abs(raw_y) < deadzone:
                            raw_y = 0.0
                    elif event.code == ecodes.ABS_RZ:
                        continue

                    now = time.monotonic()
                    dt = now - last_time
                    last_time = now

                    if dt > 0 and dt < 0.1:
                        smooth_x = smoothing * last_x + (1.0 - smoothing) * raw_x
                        smooth_y = smoothing * last_y + (1.0 - smoothing) * raw_y

                        rel_x = int(smooth_x * sensitivity * 200.0 * dt)
                        rel_y = int(smooth_y * sensitivity * 200.0 * dt)

                        if rel_x != 0 or rel_y != 0:
                            virtual_mouse.write(ecodes.EV_REL, ecodes.REL_X, rel_x)
                            virtual_mouse.write(ecodes.EV_REL, ecodes.REL_Y, rel_y)
                            virtual_mouse.syn()

                        last_x = smooth_x
                        last_y = smooth_y
        except BlockingIOError:
            time.sleep(0.01)
        except OSError:
            if running:
                time.sleep(1)
            continue
finally:
    virtual_mouse.close()
    gyro.close()
PYEOF

    local mapper_pid=$!
    echo "$mapper_pid" > "$PID_FILE"
    log_success "Gyro mapper started (PID $mapper_pid)"
}

stop_mapper() {
    if [ ! -f "$PID_FILE" ]; then
        log_warn "No gyro mapper PID file found"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid"
        log_info "Sent SIGTERM to gyro mapper (PID $pid)"
        local waited=0
        while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 5 ]; do
            sleep 0.5
            waited=$((waited + 1))
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            log_warn "Force-killed gyro mapper"
        fi
    fi
    rm -f "$PID_FILE"
    log_success "Gyro mapper stopped"
}

calibrate() {
    load_config
    log_info "Gyroscope calibration mode"
    log_info "Place device on flat, stable surface"
    log_info "Recording 5 seconds of baseline data..."

    local gyro_device
    gyro_device=$(find_gyro_device) || return 1

    python3 - "$gyro_device" <<'PYEOF'
import sys
import time

try:
    import evdev
    from evdev import ecodes
except ImportError:
    print("ERROR: python3-evdev not installed", file=sys.stderr)
    sys.exit(1)

gyro_path = sys.argv[1]
gyro = evdev.InputDevice(gyro_path)

offsets = {ecodes.ABS_RX: 0, ecodes.ABS_RY: 0, ecodes.ABS_RZ: 0}
counts = 0
start = time.monotonic()

print("Recording... Do not move the device.")
try:
    for event in gyro.read_loop():
        if event.type == ecodes.EV_ABS and event.code in offsets:
            offsets[event.code] += event.value
            counts += 1
        if time.monotonic() - start > 5.0:
            break
except KeyboardInterrupt:
    pass

if counts > 0:
    for code in offsets:
        offsets[code] = offsets[code] // counts

    print(f"\nCalibration complete ({counts} samples):")
    print(f"  ABS_RX offset: {offsets[ecodes.ABS_RX]}")
    print(f"  ABS_RY offset: {offsets[ecodes.ABS_RY]}")
    print(f"  ABS_RZ offset: {offsets[ecodes.ABS_RZ]}")
    print(f"\nWrite to config: /etc/aion/gyro-mapper.conf")
    print(f"GYRO_OFFSET_RX={offsets[ecodes.ABS_RX]}")
    print(f"GYRO_OFFSET_RY={offsets[ecodes.ABS_RY]}")
    print(f"GYRO_OFFSET_RZ={offsets[ecodes.ABS_RZ]}")
else:
    print("No gyro data received — check device permissions")

gyro.close()
PYEOF
}

show_usage() {
    cat <<EOF
Aion Gyroscope Mapper — Steam Deck / handheld gyro-to-mouse

Usage: $(basename "$0") <command>

Commands:
    --start        Start the gyro-to-mouse mapper
    --stop         Stop the gyro-to-mouse mapper
    --calibrate    Calibrate gyroscope zero-point offset
    --help         Show this help

Configuration: $CONFIG_FILE
  SENSITIVITY=1.0
  DEADZONE=0.05
  SMOOTHING=0.8
EOF
}

main() {
    local cmd="${1:---help}"

    case "$cmd" in
        --start)
            start_mapper
            ;;
        --stop)
            stop_mapper
            ;;
        --calibrate)
            calibrate
            ;;
        --help|-h|*)
            show_usage
            ;;
        *)
            log_error "Unknown command: $cmd"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
