#!/usr/bin/env bash
# Aion Phase 5: System Management + Update Tool
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"

log "=== Aion Phase 5: System Management ==="

arch-chroot /mnt /bin/bash <<'CHROOT'
    # ── aion-update: Atomic system updater ────────────────────────
    cat > /usr/bin/aion-update <<'UPDATE'
#!/usr/bin/env bash
# Aion Atomic Update System
# - Syncs packages
# - Creates Btrfs snapshot before update
# - Applies update
# - On failure: auto-rollback to snapshot

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion Update]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && { echo "Run with sudo"; exit 1; }

SNAP_DIR="/.snapshots"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SNAP_NAME="pre-update-${TIMESTAMP}"

# Detect current root subvolume
CURRENT_SUBVOL=$(mount | awk '/\/ / && /subvol=/ {print $6}' | sed 's|.*subvol=||;s|[)]||g')
if [[ "$CURRENT_SUBVOL" == "@" ]]; then
    TARGET_SLOT="@"
    ALT_SLOT="@alt"
    ALT_MOUNT="/mnt"
else
    TARGET_SLOT="@alt"
    ALT_SLOT="@"
    ALT_MOUNT="/mnt"
fi

log "Current root: ${TARGET_SLOT}"

# 1. Snapshot current root
log "Creating snapshot ${SNAP_NAME}..."
btrfs subvolume snapshot -r "/${TARGET_SLOT}" "${SNAP_DIR}/${SNAP_NAME}"

# 2. Update packages
log "Syncing packages..."
if ! pacman -Syu --noconfirm; then
    warn "Update failed! Rolling back..."
    btrfs subvolume delete "/${TARGET_SLOT}"
    btrfs subvolume snapshot "${SNAP_DIR}/${SNAP_NAME}" "/${TARGET_SLOT}"
    bootctl update
    echo "Rolled back to ${SNAP_NAME}"
    exit 1
fi

# 3. Update boot
bootctl update

# 4. Rebuild initramfs
mkinitcpio -P

# 5. Cleanup old snapshots (keep last 5)
SNAPSHOTS=($(ls -1d ${SNAP_DIR}/pre-update-* 2>/dev/null | sort -r))
if [[ ${#SNAPSHOTS[@]} -gt 5 ]]; then
    for old in "${SNAPSHOTS[@]:5}"; do
        log "Removing old snapshot: $(basename $old)"
        btrfs subvolume delete "$old" 2>/dev/null || true
    done
fi

log "Update complete! Reboot recommended."
UPDATE
    chmod +x /usr/bin/aion-update

    # ── aion-rollback: Manual rollback tool ───────────────────────
    cat > /usr/bin/aion-rollback <<'ROLLBACK'
#!/usr/bin/env bash
# Aion Rollback - restore to a previous snapshot
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && { echo "Run with sudo"; exit 1; }

SNAP_DIR="/.snapshots"

echo "Available snapshots:"
select SNAP in $(ls -1d ${SNAP_DIR}/pre-update-* 2>/dev/null | sort -r); do
    if [[ -n "$SNAP" ]]; then
        CURRENT=$(mount | awk '/\/ / && /subvol=/ {print $6}' | sed 's|.*subvol=||;s|[)]||g')
        log "Restoring $(basename $SNAP) over ${CURRENT}..."
        btrfs subvolume delete "/${CURRENT}"
        btrfs subvolume snapshot "$SNAP" "/${CURRENT}"
        bootctl update
        log "Rollback complete! Reboot."
        exit 0
    else
        echo "Invalid selection"
    fi
done
ROLLBACK
    chmod +x /usr/bin/aion-rollback

    # ── aion-mode: Quick mode switcher in terminal ────────────────
    cat > /usr/bin/aion-mode <<'MODE'
#!/usr/bin/env bash
# Quick gaming/desktop mode switch
CURRENT=$(cat /etc/aion-mode 2>/dev/null || echo "gaming")
echo "Current mode: ${CURRENT}"

case "${1:-}" in
    gaming|g)
        sed -i 's|Session=aion-desktop.desktop|Session=gamescope-session.desktop|' /etc/sddm.conf.d/aion.conf
        echo "gaming" > /etc/aion-mode
        echo "Switched to Gaming Mode. Restart session."
        ;;
    desktop|d)
        sed -i 's|Session=gamescope-session.desktop|Session=aion-desktop.desktop|' /etc/sddm.conf.d/aion.conf
        echo "desktop" > /etc/aion-mode
        echo "Switched to Desktop Mode. Restart session."
        ;;
    *)
        echo "Usage: aion-mode {gaming|desktop}"
        ;;
esac
MODE
    chmod +x /usr/bin/aion-mode

    # ── Telemetry removal ────────────────────────────────────────────
    cat > /usr/bin/aion-debloat <<'DEBLOAT'
#!/usr/bin/env bash
# Remove optional telemetry and bloat
set -euo pipefail
[[ $EUID -ne 0 ]] && { echo "Run with sudo"; exit 1; }

echo "Disabling telemetry services..."
systemctl disable --now packagekit 2>/dev/null || true
systemctl mask packagekit.service

# Disable KDE telemetry (Plasma 6 ships kwriteconfig6)
kwriteconfig6 --file kdeglobals --group KDE --key Statistics Enabled false 2>/dev/null || true

echo "Disabling unused services..."
systemctl mask avahi-daemon.service 2>/dev/null || true
systemctl mask bluetooth.service 2>/dev/null || true

echo "Debloat complete."
DEBLOAT
    chmod +x /usr/bin/aion-debloat

    # ── aion-hardware: Hardware detection + driver setup ──────────
    cat > /usr/bin/aion-hardware <<'HARDWARE'
#!/usr/bin/env bash
# Auto-detect hardware and install optimal drivers
set -euo pipefail
[[ $EUID -ne 0 ]] && { echo "Run with sudo"; exit 1; }

echo "Detecting GPU..."

if lspci | grep -qi "nvidia"; then
    echo "NVIDIA GPU detected"
    pacman -S --needed --noconfirm nvidia-utils lib32-nvidia-utils nvidia-settings opencl-nvidia
    echo "options nvidia-drm modeset=1" > /etc/modprobe.d/aion-nvidia.conf
elif lspci | grep -qi "amd"; then
    echo "AMD GPU detected"
    pacman -S --needed --noconfirm vulkan-radeon lib32-vulkan-radeon libva-mesa-driver mesa-vdpau
elif lspci | grep -qi "intel"; then
    echo "Intel GPU detected"
    pacman -S --needed --noconfirm vulkan-intel lib32-vulkan-intel intel-media-driver
fi

echo "Detecting controllers..."
if lsusb | grep -qi "xbox\|microsoft.*controller"; then
    echo "Xbox controller detected - xone driver available"
    yay -S --noconfirm xone-dkms 2>/dev/null || true
fi

echo "Hardware check complete."
HARDWARE
    chmod +x /usr/bin/aion-hardware

CHROOT

log "=== Phase 5 Complete: System management tools installed ==="
