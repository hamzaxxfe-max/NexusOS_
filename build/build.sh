#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Aion — Main Build Script
#  Arch-based immutable Gaming OS
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/output"
ISO_NAME="aion-$(date +%Y%m%d)-x86_64.iso"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

banner() {
    echo -e "${BLUE}"
    cat <<'BANNER'
 _   _                       ___  ____
| \ | | _____  ___   _ ___ / _ \/ ___|
|  \| |/ _ \ \/ / | | / __| | | \___ \
| |\  |  __/>  <| |_| \__ \ |_| |___) |
|_| \_|\___/_/\_\\__,_|___/\__\_\____/
       Gaming OS — Built on Arch Linux
BANNER
    echo -e "${NC}"
}

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Pre-flight checks ───────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Must run as root (use sudo)"
[[ ! -f /etc/arch-release ]] && err "Must build on Arch Linux"

banner

usage() {
    cat <<EOF
Usage: sudo ./build.sh [OPTIONS]

Options:
  --target-device DEVICE    Target disk for installation (e.g., /dev/sda)
  --hostname NAME           System hostname (default: aion)
  --username NAME           Primary user (default: gamer)
  --skip-gaming             Skip gaming stack installation
  --skip-desktop            Skip desktop environment
  --iso-only                Build ISO without installing
  --help                    Show this help

Environment:
  ROOT_UUID    UUID of the Btrfs root partition (required for install)
  HOSTNAME     System hostname
  USERNAME     Primary user name
  AION_USER_PASSWORD   User password (skips prompt)
  AION_ROOT_PASSWORD   Root password (skips prompt)

Examples:
  # Build ISO only
  sudo ./build.sh --iso-only

  # Install to disk
  sudo ./build.sh --target-device /dev/nvme0n1

  # Full install with custom user
  sudo ./build.sh --target-device /dev/sda --username myname --hostname mypc
EOF
    exit 0
}

# ── Parse args ───────────────────────────────────────────────────────
TARGET_DEVICE=""
SKIP_GAMING=false
SKIP_DESKTOP=false
ISO_ONLY=false
export HOSTNAME="${HOSTNAME:-aion}"
USERNAME="${USERNAME:-gamer}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --target-device) TARGET_DEVICE="$2"; shift 2;;
        --hostname) HOSTNAME="$2"; shift 2;;
        --username) USERNAME="$2"; shift 2;;
        --skip-gaming) SKIP_GAMING=true; shift;;
        --skip-desktop) SKIP_DESKTOP=true; shift;;
        --iso-only) ISO_ONLY=true; shift;;
        --help) usage;;
        *) err "Unknown option: $1";;
    esac
done

# ── ISO-only mode ────────────────────────────────────────────────────
if $ISO_ONLY; then
    log "Building ISO only..."
    mkdir -p "$BUILD_DIR"

    # Use archiso to build the live ISO
    pacman -S --needed --noconfirm archiso

    # Create archiso profile
    ISO_DIR="${BUILD_DIR}/iso-profile"
    mkdir -p "$ISO_DIR"/{airootfs,var/lib/pacman/local}

    # Copy packages list
    cat > "$ISO_DIR/packages.x86_64" <<'PKGS'
base
linux-cachyos-bore
linux-firmware
btrfs-progs
nvidia-utils
steam
gamescope
mangohud
gamemode
proton-ge-custom-bin
plasma-desktop
sddm
dolphin
konsole
firefox
flatpak
git
sudo
networkmanager
vim
nano
fastfetch
htop
btop
flatpak
yay
PKGS

    # Copy build scripts into ISO
    mkdir -p "$ISO_DIR/airootfs/usr/local/bin"
    cp "$SCRIPT_DIR"/scripts/*.sh "$ISO_DIR/airootfs/usr/local/bin/"

    # Copy configs
    mkdir -p "$ISO_DIR/airootfs/etc"
    cp -r "$SCRIPT_DIR"/../configs/* "$ISO_DIR/airootfs/etc/" 2>/dev/null || true

    # Build with archiso
    mkarchiso -w "$BUILD_DIR/work" -o "$BUILD_DIR" "$ISO_DIR"

    mv "$BUILD_DIR/aion-x86_64.iso" "$BUILD_DIR/$ISO_NAME" 2>/dev/null || true
    log "ISO built: ${BUILD_DIR}/${ISO_NAME}"
    exit 0
fi

# ── Full installation mode ───────────────────────────────────────────
[[ -z "$TARGET_DEVICE" ]] && err "--target-device required for installation"

log "=== Aion Full Installation ==="
log "Target: ${TARGET_DEVICE}"
log "Hostname: ${HOSTNAME}"
log "User: ${USERNAME}"

# Step 1: Partition
log "Step 1/6: Partitioning ${TARGET_DEVICE}..."
read -p "WARNING: This will ERASE ${TARGET_DEVICE}. Continue? (yes/no): " CONFIRM
[[ "$CONFIRM" != "yes" ]] && { echo "Aborted."; exit 0; }

# Partition layout: ESP + Btrfs root
wipefs -af "$TARGET_DEVICE"
sgdisk --zap-all "$TARGET_DEVICE"

# ESP partition (512MB)
sgdisk -n 1:0:+512M -t 1:ef00 -c 1:"EFI" "$TARGET_DEVICE"
# Root Btrfs partition (rest)
sgdisk -n 2:0:0 -t 2:8300 -c 2:"aion" "$TARGET_DEVICE"

# Format
ROOT_UUID=$(blkid -s UUID -o value "${TARGET_DEVICE}p2")
mkfs.fat -F32 -n EFI "${TARGET_DEVICE}p1"
mkfs.btrfs -f -L aion -U "$ROOT_UUID" "${TARGET_DEVICE}p2"

export ROOT_UUID

# Mount ESP
mkdir -p /mnt/boot
mount "${TARGET_DEVICE}p1" /mnt/boot

# Step 2: Base system
log "Step 2/6: Installing base system..."
bash "$SCRIPT_DIR/scripts/01-base-system.sh"

# Step 3: Gaming kernel
log "Step 3/6: Installing gaming kernel..."
bash "$SCRIPT_DIR/scripts/02-gaming-kernel.sh"

# Step 4: Gaming stack
if ! $SKIP_GAMING; then
    log "Step 4/6: Installing gaming stack..."
    bash "$SCRIPT_DIR/scripts/03-gaming-stack.sh"
else
    log "Step 4/6: Skipping gaming stack..."
fi

# Step 5: Desktop
if ! $SKIP_DESKTOP; then
    log "Step 5/6: Installing desktop environment..."
    bash "$SCRIPT_DIR/scripts/04-desktop-environment.sh"
else
    log "Step 5/6: Skipping desktop..."
fi

# Step 6: System tools
log "Step 6/6: Installing system management..."
bash "$SCRIPT_DIR/scripts/05-update-system.sh"

# Step 7: Footprint optimization
log "Step 7/7: Optimizing system footprint..."
bash "$SCRIPT_DIR/scripts/06-footprint-optimize.sh"

# ── Create user ──────────────────────────────────────────────────────
# Prompt for credentials securely (never hardcode defaults)
if [[ -z "${AION_USER_PASSWORD:-}" ]]; then
    read -r -s -p "Set password for user '${USERNAME}': " USER_PASSWORD
    echo
    read -r -s -p "Confirm password for user '${USERNAME}': " USER_PASSWORD_CONFIRM
    echo
    [[ -z "$USER_PASSWORD" ]] && err "Password cannot be empty"
    [[ "$USER_PASSWORD" != "$USER_PASSWORD_CONFIRM" ]] && err "Passwords do not match"
else
    USER_PASSWORD="${AION_USER_PASSWORD}"
fi

if [[ -z "${AION_ROOT_PASSWORD:-}" ]]; then
    read -r -s -p "Set root password: " ROOT_PASSWORD
    echo
    read -r -s -p "Confirm root password: " ROOT_PASSWORD_CONFIRM
    echo
    [[ -z "$ROOT_PASSWORD" ]] && err "Root password cannot be empty"
    [[ "$ROOT_PASSWORD" != "$ROOT_PASSWORD_CONFIRM" ]] && err "Root passwords do not match"
else
    ROOT_PASSWORD="${AION_ROOT_PASSWORD}"
fi

arch-chroot /mnt /bin/bash <<USERSETUP
    useradd -m -G wheel,video,audio,storage,rfkill,games -s /bin/bash "${USERNAME}"
    echo "${USERNAME}:${USER_PASSWORD}" | chpasswd
    echo "root:${ROOT_PASSWORD}" | chpasswd

    # Sudoers
    echo "${USERNAME} ALL=(ALL) ALL" >> /etc/sudoers.d/aion
    chmod 440 /etc/sudoers.d/aion

    # Set default user in SDDM
    sed -i "/^\[Autologin\]/a User=${USERNAME}" /etc/sddm.conf.d/aion.conf
USERSETUP

# ── Finalize ─────────────────────────────────────────────────────────
log "Cleaning up..."
arch-chroot /mnt /bin/bash <<'CLEAN'
    pacman -Scc --noconfirm
    journalctl --vacuum-time=3d
    rm -rf /tmp/* /var/tmp/*
CLEAN

umount -R /mnt

log "============================================="
log "  Aion Installation Complete!"
log "  Reboot to start gaming."
log "============================================="
log ""
log "  First boot:"
log "  - Gaming Mode starts automatically (Steam Big Picture)"
log "  - Press Ctrl+Alt+F3 → aion-mode desktop to switch"
log "  - Run: sudo aion-hardware (auto-detect GPU)"
log "  - Run: sudo aion-update (atomic system updates)"
