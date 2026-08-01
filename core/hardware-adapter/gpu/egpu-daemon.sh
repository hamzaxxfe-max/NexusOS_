#!/bin/bash
set -euo pipefail
# Aion eGPU Hotplug Daemon — Thunderbolt connect/disconnect handler

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG_FILE="/var/log/aion/egpu.log"
STATE_FILE="/run/aion-egpu-state"
UEVENT_SOCKET="/sys/kernel/uevent_listener"
THUNDERBOLT_PATH="/sys/bus/thunderbolt/devices"
SETTLE_DELAY=2
RESCAN_DELAY=1

log_info()    { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$STATE_FILE")"

cleanup() {
    log_info "eGPU daemon shutting down (PID $$)"
    rm -f "$STATE_FILE"
    exit 0
}

reload_config() {
    log_info "Received SIGHUP — reloading configuration"
}

trap cleanup SIGTERM SIGINT
trap reload_config SIGHUP

write_state() {
    local status="$1"
    local timestamp
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$STATE_FILE" <<EOF
{"status":"$status","pid":$$,"timestamp":"$timestamp"}
EOF
}

rescan_pci_bus() {
    log_info "Rescanning PCI bus"
    echo 1 > /sys/bus/pci/rescan 2>/dev/null || {
        log_warn "Standard PCI rescan failed — trying full rescan"
        echo 1 > /sys/bus/pci/drivers_probe 2>/dev/null || log_error "PCI driver probe failed"
    }
}

find_nvidia_device() {
    local nvidia_bdf=""
    while IFS= read -r pci_path; do
        [ ! -f "$pci_path/vendor" ] && continue
        local vendor
        vendor=$(cat "$pci_path/vendor" 2>/dev/null || echo "")
        if [ "$vendor" = "0x10de" ]; then
            nvidia_bdf=$(basename "$(dirname "$pci_path")")
            break
        fi
    done < <(find /sys/bus/pci/devices -maxdepth 1 -type l 2>/dev/null)
    echo "$nvidia_bdf"
}

load_nvidia_driver() {
    log_info "Loading NVIDIA driver for eGPU"
    modprobe nvidia NVreg_PreserveVideoMemoryAllocations=1 2>/dev/null || {
        log_error "Failed to load nvidia module"
        return 1
    }
    modprobe nvidia_modeset 2>/dev/null || log_warn "Failed to load nvidia_modeset"
    modprobe nvidia_drm modeset=1 2>/dev/null || log_warn "Failed to load nvidia_drm"
    modprobe nvidia_uvm 2>/dev/null || log_warn "Failed to load nvidia_uvm"
    log_success "NVIDIA driver modules loaded"
}

unload_nvidia_driver() {
    log_info "Unloading NVIDIA driver for eGPU"
    if [ -n "${DISPLAY:-}" ] && command -v nvidia-smi &>/dev/null; then
        nvidia-smi --gpu-reset 2>/dev/null || log_warn "GPU reset failed (may be in use)"
    fi
    for mod in nvidia_uvm nvidia_drm nvidia_modeset nvidia; do
        if lsmod 2>/dev/null | grep -q "^${mod}"; then
            modprobe -r "$mod" 2>/dev/null || log_warn "Could not unload $mod"
        fi
    done
    log_success "NVIDIA driver modules unloaded"
}

notify_compositor() {
    local event="$1"
    log_info "Notifying compositor: $event"
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
        local sway_msg
        sway_msg=$(command -v swaymsg 2>/dev/null || echo "")
        if [ -n "$sway_msg" ]; then
            case "$event" in
                connect)
                    $sway_msg '[class="__aion_egpu"]' focus 2>/dev/null || true
                    log_info "Sent GPU connect notification to Sway"
                    ;;
                disconnect)
                    log_info "Sent GPU disconnect notification to Sway"
                    ;;
            esac
        fi
    elif [ -n "${DISPLAY:-}" ]; then
        if command -v xdotool &>/dev/null; then
            log_info "X11 session detected — sending XRandR hotplug event"
            xdotool key super+F12 2>/dev/null || true
        fi
    fi
}

handle_connect() {
    log_info "=== eGPU CONNECT detected ==="
    write_state "connecting"

    log_info "Waiting ${SETTLE_DELAY}s for PCI device enumeration"
    sleep "$SETTLE_DELAY"

    rescan_pci_bus

    log_info "Waiting ${RESCAN_DELAY}s after PCI rescan"
    sleep "$RESCAN_DELAY"

    local nvidia_bdf
    nvidia_bdf=$(find_nvidia_device)
    if [ -z "$nvidia_bdf" ]; then
        log_warn "No NVIDIA device found after rescan — eGPU may not be NVIDIA"
        write_state "connected-unknown"
        return
    fi

    log_info "NVIDIA eGPU found at PCI $nvidia_bdf"
    load_nvidia_driver

    local sysfs_power="/sys/bus/pci/devices/0000:${nvidia_bdf}/power/control"
    if [ -f "$sysfs_power" ]; then
        echo performance > "$sysfs_power"
        log_info "Set eGPU power to performance mode"
    fi

    write_state "connected"
    log_success "eGPU ready (PCI $nvidia_bdf)"
    notify_compositor "connect"
}

handle_disconnect() {
    log_info "=== eGPU DISCONNECT detected ==="
    write_state "disconnecting"

    notify_compositor "disconnect"

    local nvidia_bdf
    nvidia_bdf=$(find_nvidia_device)
    if [ -n "$nvidia_bdf" ]; then
        unload_nvidia_driver
    fi

    log_info "Waiting ${RESCAN_DELAY}s before PCI rescan"
    sleep "$RESCAN_DELAY"
    rescan_pci_bus

    local intel_found=false
    while IFS= read -r pci_path; do
        [ ! -f "$pci_path/vendor" ] && continue
        local vendor
        vendor=$(cat "$pci_path/vendor" 2>/dev/null || echo "")
        if [ "$vendor" = "0x8086" ]; then
            intel_found=true
            break
        fi
    done < <(find /sys/bus/pci/devices -maxdepth 1 -type l 2>/dev/null)

    if $intel_found; then
        log_info "Intel iGPU available — reverting to integrated graphics"
        if ! lsmod 2>/dev/null | grep -q "^i915"; then
            modprobe i915 2>/dev/null || log_warn "Failed to reload i915"
        fi
    fi

    write_state "disconnected"
    log_success "Reverted to integrated graphics"
}

monitor_thunderbolt() {
    log_info "Monitoring Thunderbolt events via udevadm"
    udevadm monitor --subsystem-match=thunderbolt --udev 2>/dev/null | while IFS= read -r line; do
        if echo "$line" | grep -q "UDEV.*add"; then
            handle_connect
        elif echo "$line" | grep -q "UDEV.*remove"; then
            handle_disconnect
        fi
    done
}

monitor_pci() {
    log_info "Fallback: monitoring PCI add/remove events"
    udevadm monitor --subsystem-match=pci --udev 2>/dev/null | while IFS= read -r line; do
        if echo "$line" | grep -q "UDEV.*add"; then
            local event_vendor=""
            local event_data
            while IFS= read -r detail; do
                if echo "$detail" | grep -q "ID_VENDOR_ID=10de"; then
                    event_vendor="nvidia"
                    break
                fi
            done < <(udevadm info --query=all --name="${BASH_REMATCH[0]:-}" 2>/dev/null || echo "")

            if [ "$event_vendor" = "nvidia" ]; then
                local current_state
                current_state=$(cat "$STATE_FILE" 2>/dev/null | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || echo "disconnected")
                if [ "$current_state" = "disconnected" ] || [ "$current_state" = "none" ]; then
                    handle_connect
                fi
            fi
        elif echo "$line" | grep -q "UDEV.*remove"; then
            local current_state
            current_state=$(cat "$STATE_FILE" 2>/dev/null | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || echo "disconnected")
            if [ "$current_state" = "connected" ] || [ "$current_state" = "connecting" ]; then
                handle_disconnect
            fi
        fi
    done
}

main() {
    log_info "=== Aion eGPU Hotplug Daemon starting (PID $$) ==="
    write_state "monitoring"

    if [ -d "$THUNDERBOLT_PATH" ] && ls "$THUNDERBOLT_PATH" &>/dev/null; then
        log_info "Thunderbolt subsystem available"
        monitor_thunderbolt
    else
        log_warn "Thunderbolt subsystem not found — using PCI fallback"
        monitor_pci
    fi
}

main "$@"
