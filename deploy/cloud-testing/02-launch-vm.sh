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
ISO_PATH="${ISO_PATH:-}"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE.fd}"
OVMF_VARS="${OVMF_VARS:-$VM_DIR/efi/OVMF_VARS.fd}"

VNC_PORT="${VNC_PORT:-1}"
SERIAL_LOG="${VM_DIR}/logs/serial.log"
QEMU_LOG="${VM_DIR}/logs/qemu.log"
PID_FILE="$VM_DIR/qemu.pid"

SMP="${SMP:-4}"
MEMORY="${MEMORY:-8192}"
DISK_SIZE="${DISK_SIZE:-32G}"

log()    { echo -e "${CYAN}[VM]${NC} $*"; }
log_ok() { echo -e "${GREEN}[VM]${NC} $*"; }
log_warn(){ echo -e "${YELLOW}[VM]${NC} $*"; }
log_err(){ echo -e "${RED}[VM]${NC} $*"; }

check_requirements() {
    if ! command -v qemu-system-x86_64 &>/dev/null; then
        log_err "qemu-system-x86_64 not found. Run 01-setup-qemu.sh first."
        exit 1
    fi

    if [ ! -f "$OVMF_CODE" ]; then
        log_err "OVMF firmware not found at $OVMF_CODE"
        log_err "Run 01-setup-qemu.sh to install OVMF"
        exit 1
    fi

    if [ ! -f "$VM_DIR/disks/aion.qcow2" ]; then
        log_err "VM disk not found at $VM_DIR/disks/aion.qcow2"
        log_err "Run 01-setup-qemu.sh to create VM disk"
        exit 1
    fi

    if [ -n "$ISO_PATH" ] && [ ! -f "$ISO_PATH" ]; then
        log_err "ISO not found at $ISO_PATH"
        exit 1
    fi
}

detect_kvm() {
    if [ -e /dev/kvm ]; then
        KVM_ENABLE="enable"
        ACCELERATION="-accel kvm -cpu host"
        log_ok "KVM acceleration enabled"
    else
        KVM_ENABLE="disable"
        ACCELERATION="-accel tcg -cpu max"
        log_warn "KVM not available — using TCG (software emulation, slower)"
    fi
}

detect_audio() {
    AUDIO_DRIVER="none"
    if [ -e /dev/snd/ ]; then
        AUDIO_DRIVER="alsa"
    elif command -v pulseaudio &>/dev/null; then
        AUDIO_DRIVER="pa"
    elif command -v pipewire &>/dev/null; then
        AUDIO_DRIVER="pipewire"
    fi
    log "Audio driver: $AUDIO_DRIVER"
}

launch_vm() {
    mkdir -p "$VM_DIR/logs"
    mkdir -p "$(dirname "$SERIAL_LOG")"

    log "Launching Aion VM..."
    log "  CPUs: $SMP"
    log "  RAM:  ${MEMORY}MB"
    log "  Disk: $VM_DIR/disks/aion.qcow2"
    log "  VNC:  :$VNC_PORT (port $((5900 + VNC_PORT)))"
    log "  Serial: $SERIAL_LOG"
    echo ""

    QEMU_ARGS=(
        -name "aion-cloud"
        -machine q35,smm=on
        $ACCELERATION
        -smp "$SMP"
        -m "$MEMORY"
        -mem-path /dev/hugepages
        -mem-prealloc
    )

    QEMU_ARGS+=(
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,file=$OVMF_VARS"
    )

    QEMU_ARGS+=(
        -drive "if=virtio,format=qcow2,file=$VM_DIR/disks/aion.qcow2,discard=unmap,detect-zeroes=unmap"
    )

    if [ -n "$ISO_PATH" ]; then
        QEMU_ARGS+=(
            -drive "media=cdrom,file=$ISO_PATH,if=ide"
        )
    fi

    QEMU_ARGS+=(
        -netdev "user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::8080-:80"
        -device "virtio-net-pci,netdev=net0"
    )

    QEMU_ARGS+=(
        -device "virtio-vga"
        -display "vnc=:${VNC_PORT}"
    )

    QEMU_ARGS+=(
        -audiodev "${AUDIO_DRIVER},id=audio0"
        -device "intel-hda"
        -device "hda-output,audiodev=audio0"
    )

    QEMU_ARGS+=(
        -device "virtio-keyboard-pci"
        -device "virtio-mouse-pci"
    )

    QEMU_ARGS+=(
        -chardev "file,id=serial0,path=$SERIAL_LOG"
        -serial "chardev:serial0"
        -serial "mon:stdio"
    )

    QEMU_ARGS+=(
        -chardev "socket,id=mon0,path=$VM_DIR/qemu-monitor.sock,server=on,wait=off"
        -mon "chardev=mon0,mode=readline"
    )

    QEMU_ARGS+=(
        -pidfile "$PID_FILE"
        -daemonize
    )

    if [ "$KVM_ENABLE" = "disable" ]; then
        QEMU_ARGS=("${QEMU_ARGS[@]/-mem-path \/dev\/hugepages/}")
        QEMU_ARGS=("${QEMU_ARGS[@]/-mem-prealloc/}")
    fi

    qemu-system-x86_64 "${QEMU_ARGS[@]}" >> "$QEMU_LOG" 2>&1

    sleep 2

    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_ok "VM started (PID: $pid)"
            log_ok "VNC: vnc://$(hostname -I | awk '{print $1}'):$((5900 + VNC_PORT))"
            log_ok "Monitor: socat UNIX-CONNECT:$VM_DIR/qemu-monitor.sock"
        else
            log_err "VM process not running. Check $QEMU_LOG"
            exit 1
        fi
    else
        log_err "PID file not created. Check $QEMU_LOG"
        exit 1
    fi
}

print_connection_info() {
    local ip
    ip=$(hostname -I | awk '{print $1}')
    [ -z "$ip" ] && ip="<server-ip>"

    echo ""
    echo "=============================================="
    echo "  Aion VM Running"
    echo "=============================================="
    echo ""
    echo "  VNC Connection:"
    echo "    vnc://${ip}:$((5900 + VNC_PORT))"
    echo ""
    echo "  QEMU Monitor:"
    echo "    socat UNIX-CONNECT:$VM_DIR/qemu-monitor.sock readline"
    echo ""
    echo "  Serial Log (live):"
    echo "    tail -f $SERIAL_LOG"
    echo ""
    echo "  QEMU Log:"
    echo "    tail -f $QEMU_LOG"
    echo ""
    echo "  Stop VM:"
    echo "    kill -TERM \$(cat $PID_FILE)"
    echo ""
    echo "  Restart VM:"
    echo "    kill -TERM \$(cat $PID_FILE) && sleep 2 && sudo bash $SCRIPT_DIR/02-launch-vm.sh"
    echo ""
    echo "  Next: Run 03-setup-novnc.sh for browser access"
    echo ""
    echo "=============================================="
}

main() {
    log "=== Aion Cloud VM Launcher ==="
    check_requirements
    detect_kvm
    detect_audio
    launch_vm
    print_connection_info
}

main "$@"
