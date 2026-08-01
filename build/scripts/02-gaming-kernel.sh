#!/usr/bin/env bash
# Aion Phase 2: Gaming Kernel (CachyOS BORE + Pre-compiled NVIDIA)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Single source of truth for subvolume names + label.
CONSTANTS="${SCRIPT_DIR}/../constants.sh"
[[ -f "${CONSTANTS}" ]] && . "${CONSTANTS}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"
[[ -z "$ROOT_UUID" ]] && err "ROOT_UUID required (UUID of target Btrfs partition)"
export ROOT_UUID SUBVOL_ROOT SUBVOL_ALT

log "=== Aion Phase 2: Gaming Kernel ==="

arch-chroot /mnt /bin/bash <<'CHROOT'
    # ── Add CachyOS repos ────────────────────────────────────────────
    log "Adding CachyOS kernel repository..."
    curl -s --fail https://mirror.cachyos.org/cachyos.repo.key \
        | pacman-key --add - 2>/dev/null
    pacman-key --lsign-key 5E1ABF44AC08BF54

    # Dedicated mirrorlist for CachyOS only. Do NOT touch /etc/pacman.d/mirrorlist
    # (Arch core/extra/multilib must keep their own mirrors).
    cat > /etc/pacman.d/cachyos-mirrorlist <<'MIRROR'
## Aion: CachyOS repos only (gaming kernel + pre-compiled NVIDIA).
## Note: mirror.cachyos.org uses /repo/$arch/$repo, not /$repo/$arch.
Server = https://mirror.cachyos.org/repo/$arch/$repo
MIRROR

    cat >> /etc/pacman.conf <<'REPO'

# CachyOS repos (gaming kernel + NVIDIA pre-compiled)
[cachyos]
Include = /etc/pacman.d/cachyos-mirrorlist
SigLevel = Required DatabaseOptional

[cachyos-core]
Include = /etc/pacman.d/cachyos-mirrorlist
SigLevel = Required DatabaseOptional

[cachyos-extra]
Include = /etc/pacman.d/cachyos-mirrorlist
SigLevel = Required DatabaseOptional
REPO

    pacman -Syu --noconfirm

    # ── Install CachyOS BORE kernel ──────────────────────────────────
    pacman -S --noconfirm linux-cachyos-bore
    pacman -S --noconfirm linux-cachyos-bore-headers

    # ── Pre-compiled NVIDIA driver (matching kernel) ─────────────────
    # CRITICAL: Never use dkms for NVIDIA on immutable root.
    # CachyOS provides pre-compiled nvidia modules for each kernel.
    # This ensures no black screen after kernel updates.

    # Detect NVIDIA GPU
    if lspci | grep -qi "nvidia"; then
        log "NVIDIA GPU detected. Installing pre-compiled drivers..."

        # Install NVIDIA packages from CachyOS repos (pre-matched to kernel)
        pacman -S --noconfirm \
            nvidia-utils \
            lib32-nvidia-utils \
            nvidia-settings \
            opencl-nvidia \
            lib32-opencl-nvidia

        # Install NVIDIA kernel module (pre-compiled, NOT dkms)
        # CachyOS provides: nvidia-cachyos-bore (pre-built for linux-cachyos-bore)
        pacman -S --noconfirm nvidia-cachyos-bore 2>/dev/null || \
            warn "Pre-compiled NVIDIA module not found in CachyOS repos"

        # DRM kernel modesetting (required for Wayland + gaming)
        mkdir -p /etc/modprobe.d
        cat > /etc/modprobe.d/aion-nvidia.conf << NVIDIA_CONF
# Aion NVIDIA Configuration
# DRM modeset for Wayland + Gamescope
options nvidia-drm modeset=1
options nvidia-drm fbdev=1

# Power management (prevent GPU from sleeping in games)
options nvidia NVreg_PreserveVideoMemoryAllocations=1
options nvidia NVreg_TemporaryFilePath=/var/tmp

# Disable GVO (Green Voodoo Overlord) for consistent frame pacing
options nvidia NVreg_EnableGpuFirmware=0
NVIDIA_CONF

        # Enable NVIDIA suspend/resume services
        systemctl enable nvidia-suspend.service 2>/dev/null || true
        systemctl enable nvidia-resume.service 2>/dev/null || true
        systemctl enable nvidia-hibernate.service 2>/dev/null || true

        log "NVIDIA pre-compiled drivers installed (no dkms)"
    else
        # AMD or Intel — install open-source drivers
        if lspci | grep -qi "amd"; then
            log "AMD GPU detected. Installing Vulkan RADV..."
            pacman -S --noconfirm \
                vulkan-radeon \
                lib32-vulkan-radeon \
                libva-mesa-driver \
                mesa-vdpau
        elif lspci | grep -qi "intel"; then
            log "Intel GPU detected. Installing Vulkan ANV..."
            pacman -S --noconfirm \
                vulkan-intel \
                lib32-vulkan-intel \
                intel-media-driver
        fi
    fi

    # ── Gaming kernel parameters ─────────────────────────────────────
    # Update boot entries for active + alternate slots ($ROOT_UUID is
    # exported from the outer shell; subvols read from constants).
    for ENTRY_SUBVOL in "${SUBVOL_ROOT}:aion-active" "${SUBVOL_ALT}:aion-alt"; do
        SUBVOL="${ENTRY_SUBVOL%%:*}"
        ENTRY_NAME="${ENTRY_SUBVOL##*:}"
        ENTRY="/boot/loader/entries/${ENTRY_NAME}.conf"
        if [[ -f "$ENTRY" ]]; then
            sed -i "s|options .*|options root=UUID=${ROOT_UUID} rootflags=subvol=${SUBVOL} rw mitigations=off nowatchdog tsc=reliable clocksource=tsc tsc=unstable nvidia-drm.modeset=1 nvidia-drm.fbdev=1 rd.udev.log_priority=3 vt.global_cursor_default=0 loglevel=3 splash systemd.unified_cgroup_hierarchy=1|" "$ENTRY"
        fi
    done

    # Rebuild initramfs with NVIDIA modules
    mkinitcpio -P

CHROOT

log "=== Phase 2 Complete: Gaming kernel + pre-compiled NVIDIA ==="
