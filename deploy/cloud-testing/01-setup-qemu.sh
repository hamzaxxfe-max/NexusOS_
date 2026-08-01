#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG="/var/log/aion-cloud-setup.log"
mkdir -p "$(dirname "$LOG")"

log()    { echo -e "${CYAN}[SETUP]${NC} $*"; echo "[SETUP] $(date '+%H:%M:%S') $*" >> "$LOG"; }
log_ok() { echo -e "${GREEN}[SETUP]${NC} $*"; echo "[SETUP] OK $(date '+%H:%M:%S') $*" >> "$LOG"; }
log_warn(){ echo -e "${YELLOW}[SETUP]${NC} $*"; echo "[SETUP] WARN $(date '+%H:%M:%S') $*" >> "$LOG"; }
log_err(){ echo -e "${RED}[SETUP]${NC} $*"; echo "[SETUP] ERR $(date '+%H:%M:%S') $*" >> "$LOG"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO_PATH="${ISO_PATH:-$SCRIPT_DIR/../../build/aion-1.0.0.iso}"
VM_DIR="${VM_DIR:-/var/lib/aion-vm}"
OVMF_CODE="/usr/share/OVMF/OVMF_CODE.fd"
OVMF_VARS="/usr/share/OVMF/OVMF_VARS.fd"

echo "=============================================="
echo "  Aion Cloud VM — Environment Setup"
echo "=============================================="
echo ""

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_err "Must run as root"
        exit 1
    fi
}

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        echo "unknown"
    fi
}

install_qemu_kvm() {
    log "Installing QEMU/KVM and dependencies..."

    local os
    os=$(detect_os)

    case "$os" in
        ubuntu|debian)
            apt-get update -qq
            apt-get install -y -qq \
                qemu-kvm \
                qemu-system-x86 \
                qemu-utils \
                ovmf \
                libguestfs-tools \
                virt-manager \
                libvirt-daemon-system \
                libvirt-clients \
                bridge-utils \
                netcat-openbsd \
                socat \
                tmux \
                wget \
                curl \
                git \
                python3 \
                python3-pip \
                python3-venv \
                unzip
            ;;
        fedora|rhel|centos|rocky|alma)
            dnf install -y \
                qemu-kvm \
                qemu-system-x86 \
                qemu-img \
                edk2-ovmf \
                libguestfs-tools \
                virt-manager \
                libvirt-daemon-system \
                libvirt-clients \
                bridge-utils \
                nmap-ncat \
                socat \
                tmux \
                wget \
                curl \
                git \
                python3 \
                python3-pip
            ;;
        arch|manjaro)
            pacman -Sy --noconfirm \
                qemu-full \
                edk2-ovmf \
                libvirt \
                virt-manager \
                bridge-utils \
                socat \
                tmux \
                wget \
                curl \
                git \
                python \
                python-pip
            ;;
        *)
            log_err "Unsupported OS: $os"
            log_warn "Attempting Ubuntu/Debian installation..."
            apt-get update -qq
            apt-get install -y -qq qemu-kvm qemu-system-x86 qemu-utils ovmf socat tmux wget curl git python3 python3-pip
            ;;
    esac

    log_ok "QEMU/KVM installed"
}

enable_kvm() {
    log "Enabling KVM acceleration..."

    if [ ! -e /dev/kvm ]; then
        log_warn "/dev/kvm not found — KVM acceleration may not be available"
        log_warn "VM will run in TCG (software emulation) mode — slower but functional"
        return
    fi

    chmod 666 /dev/kvm 2>/dev/null || true

    if lsmod | grep -q kvm; then
        log_ok "KVM modules loaded"
    else
        modprobe kvm 2>/dev/null || true
        modprobe kvm_intel 2>/dev/null || true
        modprobe kvm_amd 2>/dev/null || true
        log_ok "KVM modules loaded"
    fi
}

setup_ovmf() {
    log "Verifying OVMF firmware for UEFI boot..."

    if [ -f "$OVMF_CODE" ]; then
        log_ok "OVMF_CODE found: $OVMF_CODE"
    else
        log_warn "OVMF_CODE not found at $OVMF_CODE"
        for candidate in \
            /usr/share/edk2/x64/OVMF_CODE.fd \
            /usr/share/edk2/ovmf/OVMF_CODE.fd \
            /usr/share/qemu/OVMF_CODE.fd \
            /usr/share/OVMF/OVMF_CODE.4m; do
            if [ -f "$candidate" ]; then
                OVMF_CODE="$candidate"
                log_ok "Found OVMF_CODE at $candidate"
                break
            fi
        done
    fi

    if [ -f "$OVMF_VARS" ]; then
        log_ok "OVMF_VARS found: $OVMF_VARS"
    else
        for candidate in \
            /usr/share/edk2/x64/OVMF_VARS.fd \
            /usr/share/edk2/ovmf/OVMF_VARS.fd \
            /usr/share/qemu/OVMF_VARS.fd \
            /usr/share/OVMF/OVMF_VARS.4m; do
            if [ -f "$candidate" ]; then
                OVMF_VARS="$candidate"
                log_ok "Found OVMF_VARS at $candidate"
                break
            fi
        done
    fi

    mkdir -p "$VM_DIR/efi"
    cp "$OVMF_VARS" "$VM_DIR/efi/OVMF_VARS.fd" 2>/dev/null || true
    log_ok "OVMF firmware ready"
}

setup_vm_directory() {
    log "Setting up VM directory..."

    mkdir -p "$VM_DIR"/{disks,efi,logs,snapshots}

    if [ ! -f "$VM_DIR/disks/aion.qcow2" ]; then
        log "Creating 32GB VM disk image..."
        qemu-img create -f qcow2 "$VM_DIR/disks/aion.qcow2" 32G
        log_ok "VM disk created: $VM_DIR/disks/aion.qcow2"
    else
        log_ok "VM disk already exists"
    fi

    log_ok "VM directory ready: $VM_DIR"
}

check_iso() {
    log "Checking for Aion ISO..."

    if [ -f "$ISO_PATH" ]; then
        local iso_size
        iso_size=$(stat -c%s "$ISO_PATH" 2>/dev/null || stat --printf="%s" "$ISO_PATH" 2>/dev/null || echo "0")
        iso_size_mb=$((iso_size / 1024 / 1024))
        log_ok "ISO found: $ISO_PATH (${iso_size_mb}MB)"
    else
        log_warn "ISO not found at $ISO_PATH"
        log_warn "Set ISO_PATH environment variable to your ISO location"
        log_warn "Example: export ISO_PATH=/path/to/aion-1.0.0.iso"
    fi

    echo "OVMF_CODE=$OVMF_CODE" > "$VM_DIR/.env"
    echo "OVMF_VARS=$VM_DIR/efi/OVMF_VARS.fd" >> "$VM_DIR/.env"
    echo "VM_DIR=$VM_DIR" >> "$VM_DIR/.env"
    echo "ISO_PATH=$ISO_PATH" >> "$VM_DIR/.env"
    log_ok "Environment saved to $VM_DIR/.env"
}

install_novnc_deps() {
    log "Installing noVNC dependencies..."

    pip3 install websockify 2>/dev/null || pip install websockify 2>/dev/null || true

    NOVNC_DIR="/opt/noVNC"
    if [ ! -d "$NOVNC_DIR" ]; then
        git clone --depth 1 https://github.com/novnc/noVNC.git "$NOVNC_DIR" 2>/dev/null || true
        ln -sf "$NOVNC_DIR/vnc.html" "$NOVNC_DIR/index.html" 2>/dev/null || true
    fi

    if command -v websockify &>/dev/null; then
        log_ok "websockify installed"
    else
        log_warn "websockify not in PATH — will install in launch script"
    fi

    log_ok "noVNC ready at $NOVNC_DIR"
}

print_summary() {
    echo ""
    echo "=============================================="
    echo "  Setup Complete"
    echo "=============================================="
    echo ""
    echo "  VM Directory: $VM_DIR"
    echo "  OVMF Code:    $OVMF_CODE"
    echo "  OVMF Vars:    $VM_DIR/efi/OVMF_VARS.fd"
    echo "  VM Disk:      $VM_DIR/disks/aion.qcow2"
    echo "  ISO:          $ISO_PATH"
    echo "  Environment:  $VM_DIR/.env"
    echo ""
    echo "  Next steps:"
    echo "    1. Place your ISO: export ISO_PATH=/path/to/aion.iso"
    echo "    2. Launch VM:      sudo bash $SCRIPT_DIR/02-launch-vm.sh"
    echo "    3. Start noVNC:    sudo bash $SCRIPT_DIR/03-setup-novnc.sh"
    echo "    4. Monitor boot:   sudo bash $SCRIPT_DIR/04-boot-monitor.sh"
    echo ""
    echo "=============================================="
}

main() {
    check_root
    install_qemu_kvm
    enable_kvm
    setup_ovmf
    setup_vm_directory
    check_iso
    install_novnc_deps
    print_summary
}

main "$@"
