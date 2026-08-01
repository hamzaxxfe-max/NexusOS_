#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${VM_DIR:-/var/lib/aion-vm}/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

VM_DIR="${VM_DIR:-/var/lib/aion-vm}"
VNC_PORT="${VNC_PORT:-1}"
SERIAL_LOG="${VM_DIR}/logs/serial-$(date +%Y%m%d_%H%M%S).log"
BOOT_LOG="${VM_DIR}/logs/cloud_boot.log"
PID_FILE="$VM_DIR/qemu.pid"
MONITOR_SOCK="$VM_DIR/qemu-monitor.sock"

log()    { echo -e "${CYAN}[MON]${NC} $*"; }
log_ok() { echo -e "${GREEN}[MON]${NC} $*"; }
log_warn(){ echo -e "${YELLOW}[MON]${NC} $*"; }
log_err(){ echo -e "${RED}[MON]${NC} $*"; }

check_vm_running() {
    if [ ! -f "$PID_FILE" ]; then
        log_err "VM not running (no PID file). Launch with 02-launch-vm.sh first."
        exit 1
    fi

    local pid
    pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        log_err "VM process $pid not running"
        exit 1
    fi

    log_ok "VM running (PID: $pid)"
}

wait_for_serial() {
    log "Waiting for serial log to appear..."

    local attempts=0
    while [ ! -f "$SERIAL_LOG" ] && [ $attempts -lt 30 ]; do
        sleep 1
        attempts=$((attempts + 1))
    done

    if [ ! -f "$SERIAL_LOG" ]; then
        log_warn "Serial log not found at $SERIAL_LOG"
        log_warn "Trying QEMU monitor instead..."

        if [ -S "$MONITOR_SOCK" ]; then
            echo "info status" | socat - UNIX-CONNECT:"$MONITOR_SOCK" 2>/dev/null || true
            echo "info qtree" | socat - UNIX-CONNECT:"$MONITOR_SOCK" 2>/dev/null | head -20 || true
        fi
    else
        log_ok "Serial log found: $SERIAL_LOG"
    fi
}

capture_boot_logs() {
    log "Capturing boot logs..."

    echo "# Aion Cloud Boot Log" > "$BOOT_LOG"
    echo "# Captured: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "$BOOT_LOG"
    echo "# VM PID: $(cat "$PID_FILE" 2>/dev/null || echo 'unknown')" >> "$BOOT_LOG"
    echo "#" >> "$BOOT_LOG"
    echo "" >> "$BOOT_LOG"

    if [ -f "$SERIAL_LOG" ]; then
        log "Serial console output available at: $SERIAL_LOG"
        cp "$SERIAL_LOG" "$BOOT_LOG"
    fi

    echo "" >> "$BOOT_LOG"
    echo "=== VM Boot Log Captured ===" >> "$BOOT_LOG"
    echo "Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "$BOOT_LOG"
    echo "Host: $(hostname)" >> "$BOOT_LOG"
    echo "" >> "$BOOT_LOG"

    log_ok "Boot log saved: $BOOT_LOG"
}

analyze_gpu_autodetect() {
    log "Analyzing GPU auto-detect results..."

    local gpu_log=""
    if [ -f "$SERIAL_LOG" ]; then
        gpu_log="$SERIAL_LOG"
    elif [ -f "$BOOT_LOG" ]; then
        gpu_log="$BOOT_LOG"
    fi

    if [ -z "$gpu_log" ]; then
        log_warn "No log file to analyze"
        return
    fi

    echo "" >> "$BOOT_LOG"
    echo "=== GPU Auto-Detect Analysis ===" >> "$BOOT_LOG"

    if grep -qi "NVIDIA GPU" "$gpu_log"; then
        log_ok "NVIDIA GPU detected in VM"
        echo "GPU: NVIDIA detected" >> "$BOOT_LOG"
    elif grep -qi "AMD GPU\|amdgpu" "$gpu_log"; then
        log_ok "AMD GPU detected in VM"
        echo "GPU: AMD detected" >> "$BOOT_LOG"
    elif grep -qi "Intel GPU\|i915" "$gpu_log"; then
        log_ok "Intel GPU detected in VM"
        echo "GPU: Intel detected" >> "$BOOT_LOG"
    else
        echo "GPU: No hardware GPU (software rendering expected in QEMU)" >> "$BOOT_LOG"
    fi

    if grep -qi "nouveau.*blacklist\|blacklist.*nouveau" "$gpu_log"; then
        echo "Nouveau: Blacklisted" >> "$BOOT_LOG"
        log_ok "nouveau properly blacklisted"
    fi

    if grep -qi "modeset=1\|nvidia-drm.*modeset" "$gpu_log"; then
        echo "DRM Modeset: Enabled" >> "$BOOT_LOG"
        log_ok "DRM modeset enabled"
    fi

    if grep -qi "initramfs\|mkinitcpio" "$gpu_log"; then
        echo "Initramfs: Rebuilt with GPU modules" >> "$BOOT_LOG"
        log_ok "initramfs rebuilt with GPU modules"
    fi

    echo "" >> "$BOOT_LOG"
}

analyze_kernel_health() {
    log "Analyzing kernel health..."

    echo "=== Kernel Health Analysis ===" >> "$BOOT_LOG"

    local has_panic=false
    local has_oom=false
    local has_mount_error=false

    if [ -f "$SERIAL_LOG" ]; then
        if grep -qi "kernel panic" "$SERIAL_LOG"; then
            has_panic=true
            log_err "KERNEL PANIC detected!"
            echo "Kernel Panic: YES" >> "$BOOT_LOG"
        else
            echo "Kernel Panic: No" >> "$BOOT_LOG"
            log_ok "No kernel panic detected"
        fi

        if grep -qi "out of memory\|oom-killer\|oom_reaper" "$SERIAL_LOG"; then
            has_oom=true
            log_err "OOM killer invoked!"
            echo "OOM: YES" >> "$BOOT_LOG"
        else
            echo "OOM: No" >> "$BOOT_LOG"
            log_ok "No OOM events"
        fi

        if grep -qi "mount.*error\|failed to mount\|cannot mount" "$SERIAL_LOG"; then
            has_mount_error=true
            log_err "Mount errors detected!"
            echo "Mount Errors: YES" >> "$BOOT_LOG"
        else
            echo "Mount Errors: No" >> "$BOOT_LOG"
            log_ok "No mount errors"
        fi

        if grep -qi "immutable\|read-only\|btrfs.*readonly" "$SERIAL_LOG"; then
            echo "Immutable Root: Configured" >> "$BOOT_LOG"
            log_ok "Immutable root configured"
        fi
    else
        echo "Log file not available for analysis" >> "$BOOT_LOG"
    fi

    echo "" >> "$BOOT_LOG"

    if $has_panic || $has_oom || $has_mount_error; then
        log_err "Critical issues found — check $BOOT_LOG"
        return 1
    fi

    log_ok "Kernel health: all checks passed"
    return 0
}

analyze_systemd_boot() {
    log "Analyzing systemd-boot configuration..."

    echo "=== systemd-boot Analysis ===" >> "$BOOT_LOG"

    if [ -f "$SERIAL_LOG" ]; then
        if grep -qi "systemd-boot\|loader.*entry" "$SERIAL_LOG"; then
            echo "Boot Loader: systemd-boot detected" >> "$BOOT_LOG"
            log_ok "systemd-boot detected"
        else
            echo "Boot Loader: Unknown" >> "$BOOT_LOG"
        fi

        if grep -qi "bless\|boot.*counter\|rollback\|slot [AB]" "$SERIAL_LOG"; then
            echo "A/B Boot: Active" >> "$BOOT_LOG"
            log_ok "A/B boot system active"
        else
            echo "A/B Boot: Not detected in log" >> "$BOOT_LOG"
        fi

        if grep -qi "aion-update\|ota.*timer" "$SERIAL_LOG"; then
            echo "OTA Timer: Active" >> "$BOOT_LOG"
            log_ok "OTA timer configured"
        else
            echo "OTA Timer: Not detected in log" >> "$BOOT_LOG"
        fi

        if grep -qi "calamares\|installer" "$SERIAL_LOG"; then
            echo "Installer: Calamares detected" >> "$BOOT_LOG"
            log_ok "Calamares installer detected"
        fi
    fi

    echo "" >> "$BOOT_LOG"
}

monitor_live() {
    log "Starting live boot monitor (Ctrl+C to stop)..."
    echo ""

    if [ -f "$SERIAL_LOG" ]; then
        tail -f "$SERIAL_LOG" 2>/dev/null &
        TAIL_PID=$!
    fi

    while true; do
        if [ -f "$PID_FILE" ]; then
            local pid
            pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
            if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                log_warn "VM process $pid has exited"
                break
            fi
        fi

        sleep 5
    done

    kill "$TAIL_PID" 2>/dev/null || true
}

print_summary() {
    echo ""
    echo "=============================================="
    echo "  Boot Monitor Summary"
    echo "=============================================="
    echo ""
    echo "  Serial Log:    $SERIAL_LOG"
    echo "  Boot Log:      $BOOT_LOG"
    echo "  VM PID:        $(cat "$PID_FILE" 2>/dev/null || echo 'not running')"
    echo ""
    echo "  View live boot:"
    echo "    tail -f $SERIAL_LOG"
    echo ""
    echo "  View boot analysis:"
    echo "    cat $BOOT_LOG"
    echo ""
    echo "  Query VM status via API:"
    echo "    curl http://localhost:6082/api/status"
    echo ""
    echo "=============================================="
}

main() {
    log "=== Aion Boot Monitor ==="
    check_vm_running
    wait_for_serial
    capture_boot_logs
    analyze_gpu_autodetect
    analyze_kernel_health
    analyze_systemd_boot
    print_summary
}

if [ "${1:-}" = "--live" ]; then
    main
    monitor_live
else
    main
fi
