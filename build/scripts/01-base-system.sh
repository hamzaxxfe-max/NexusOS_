#!/usr/bin/env bash
# Aion Phase 1: Base Arch System + Immutable Root (Btrfs A/B + BLS + Auto Rollback)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Single source of truth for subvolume names + label.
CONSTANTS="${SCRIPT_DIR}/../constants.sh"
[[ -f "${CONSTANTS}" ]] && . "${CONSTANTS}"

WORK_DIR="${WORK_DIR:-/var/lib/aion-build}"
ROOT_UUID="${ROOT_UUID:-}"
HOSTNAME="${HOSTNAME:-aion}"
# Raw root partition device, e.g. /dev/sda2 — passed by build.sh.
ROOT_DEV="${1:-${ROOT_DEV:-}}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"
[[ -z "$ROOT_UUID" ]] && err "ROOT_UUID required (UUID of target Btrfs partition)"
[[ -z "$ROOT_DEV" ]] && err "Root partition device required (pass as \$1, e.g. /dev/sda2)"
[[ -b "$ROOT_DEV" ]] || err "Not a block device: ${ROOT_DEV}"

log "=== Aion Phase 1: Base System ==="

# ── Partitioning & Btrfs dual-root layout ────────────────────────────
# The partition is already formatted by build.sh (or the installer). We
# target the RAW device, never /dev/disk/by-label (label does not exist
# before the first mkfs and udev may not have settled).
log "Formatting Btrfs with dual-root layout (label=${LABEL_AION})..."
mkfs.btrfs -f -L "${LABEL_AION}" -U "$ROOT_UUID" "${ROOT_DEV}"

mount "${ROOT_DEV}" /mnt

# Create Btrfs subvolumes — dual root slots for atomic updates
btrfs subvolume create "/mnt/${SUBVOL_ROOT}"
btrfs subvolume create "/mnt/${SUBVOL_ALT}"
btrfs subvolume create "/mnt/${SUBVOL_HOME}"
btrfs subvolume create "/mnt/${SUBVOL_VAR}"
btrfs subvolume create "/mnt/${SUBVOL_SNAP}"
btrfs subvolume create "/mnt/${SUBVOL_SWAP}"

umount /mnt

# Mount active slot as default
mount -o compress=zstd,noatime,subvol="${SUBVOL_ROOT}" "${ROOT_DEV}" /mnt
mkdir -p /mnt/home /mnt/var /mnt/.snapshots /mnt/swap /mnt/alt

mount -o compress=zstd,noatime,subvol="${SUBVOL_HOME}" "${ROOT_DEV}" /mnt/home
mount -o compress=zstd,noatime,subvol="${SUBVOL_VAR}" "${ROOT_DEV}" /mnt/var
mount -o compress=zstd,noatime,subvol="${SUBVOL_SNAP}" "${ROOT_DEV}" /mnt/.snapshots
mount -o compress=zstd,noatime,subvol="${SUBVOL_SWAP}" "${ROOT_DEV}" /mnt/swap
mount -o compress=zstd,noatime,subvol="${SUBVOL_ALT}" "${ROOT_DEV}" /mnt/alt

# ── Pacstrap ─────────────────────────────────────────────────────────
log "Installing base system..."
pacstrap /mnt \
    base linux linux-firmware \
    btrfs-progs \
    systemd-boot \
    plymouth \
    networkmanager \
    sudo \
    git \
    base-devel

# ── fstab ────────────────────────────────────────────────────────────
log "Generating fstab..."
genfstab -U /mnt >> /mnt/etc/fstab

# ── BLS + dual-root + Auto Rollback setup ────────────────────────────
log "Configuring BLS dual-root boot with auto rollback..."

# ── Plymouth boot splash theme ────────────────────────────────────────
# Install Aion Neon theme into the target before mkinitcpio rebuilds.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME_DIR="${SCRIPT_DIR}/../../boot/plymouth/aion-neon"
if [[ -d "${THEME_DIR}" ]]; then
    mkdir -p /mnt/usr/share/plymouth/themes/aion-neon
    cp -a "${THEME_DIR}/"* /mnt/usr/share/plymouth/themes/aion-neon/
    mkdir -p /mnt/etc/plymouth
    cat > /mnt/etc/plymouth/plymouthd.conf <<'PLYCONF'
[Daemon]
Theme=aion-neon
ShowDelay=0
DeviceTimeout=8
PLYCONF
    log "  aion-neon Plymouth theme installed"
else
    warn "  Plymouth theme not found: ${THEME_DIR}"
fi

arch-chroot /mnt /bin/bash <<'CHROOT'
    # Hostname
    echo "aion" > /etc/hostname

    # Timezone
    ln -sf /usr/share/zoneinfo/UTC /etc/localtime
    hwclock --systohc

    # Locale
    echo "en_US.UTF-8 UTF-8" > /etc/locale.gen
    locale-gen
    echo "LANG=en_US.UTF-8" > /etc/locale.conf

    # ── Overlay for /etc (writable on read-only root) ────────────────
    mkdir -p /etc/overlay-upper /etc/overlay-work /etc/overlay-merged

    cat > /etc/systemd/system/overlay-etc.service <<'UNIT'
[Unit]
Description=Aion writable /etc overlay
Before=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/mount -t overlay overlay \
    -o lowerdir=/etc,upperdir=/etc/overlay-upper,workdir=/etc/overlay-work \
    /etc/overlay-merged
ExecStop=/usr/bin/umount /etc/overlay-merged

[Install]
WantedBy=multi-user.target
UNIT

    systemctl enable overlay-etc.service

    # ── Boot Loader Specification (BLS) with systemd-boot ────────────
    bootctl install
    mkdir -p /boot/loader/entries

    cat > /boot/loader/loader.conf <<'LOADER'
default aion-active.conf
timeout 3
console-mode auto
editor no
LOADER

    # ── systemd-bless-boot for auto rollback ────────────────────────
    # After 3 consecutive failed boots, systemd-boot auto-reverts
    # to the last known-good slot
    cat > /etc/systemd/system/aion-ab-manager.service <<'AB_SERVICE'
[Unit]
Description=Aion A/B Slot Manager
After=local-fs.target
Before=initrd-switch-root.target

[Service]
Type=oneshot
ExecStart=/usr/bin/aion-ab-manager activate
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
AB_SERVICE

    # A/B manager script
    cat > /usr/bin/aion-ab-manager <<'AB_MANAGER'
#!/usr/bin/env bash
# Aion dual-root Slot Manager
# Manages boot slot selection and auto-rollback between @ and @alt
set -euo pipefail

AB_STATE="/etc/aion-ab-state"
BOOT_ENTRIES="/boot/loader/entries"
LOADER_CONF="/boot/loader/loader.conf"
ACTIVE_CONF="aion-active.conf"
ALT_CONF="aion-alt.conf"

get_active_slot() {
    local title
    title=$(grep "^title" "${BOOT_ENTRIES}/"*.conf 2>/dev/null | grep "active" | head -1)
    if [[ "$title" == *"Active Slot"* ]]; then
        echo "active"
    else
        echo "alt"
    fi
}

get_inactive_slot() {
    local active
    active=$(get_active_slot)
    if [[ "$active" == "active" ]]; then
        echo "alt"
    else
        echo "active"
    fi
}

set_active_slot() {
    local slot="$1"
    local other
    other=$(if [[ "$slot" == "active" ]]; then echo "alt"; else echo "active"; fi)

    # Mark active slot
    sed -i "s/Slot .*/Slot ${slot} (active)/" "${BOOT_ENTRIES}/aion-${slot}.conf"
    # Mark other as inactive
    sed -i "s/Slot .* (active)/Slot ${other}/" "${BOOT_ENTRIES}/aion-${other}.conf"

    # Set default boot
    sed -i "s/^default .*/default aion-${slot}.conf/" "$LOADER_CONF"

    # Record state
    echo "active_slot=${slot}" > "$AB_STATE"
    echo "boot_count=0" >> "$AB_STATE"
    echo "last_good=$(date -Iseconds)" >> "$AB_STATE"
}

increment_boot_count() {
    local count=0
    if [[ -f "$AB_STATE" ]]; then
        count=$(grep "^boot_count=" "$AB_STATE" | cut -d= -f2)
    fi
    count=$((count + 1))

    if [[ $count -ge 3 ]]; then
        echo "3 consecutive failed boots detected. Rolling back..."
        rollback
        exit 0
    fi

    # Update state
    if [[ -f "$AB_STATE" ]]; then
        sed -i "s/^boot_count=.*/boot_count=${count}/" "$AB_STATE"
    else
        echo "active_slot=$(get_active_slot)" > "$AB_STATE"
        echo "boot_count=${count}" >> "$AB_STATE"
    fi
}

mark_good() {
    if [[ -f "$AB_STATE" ]]; then
        sed -i "s/^boot_count=.*/boot_count=0/" "$AB_STATE"
        sed -i "s/^last_good=.*/last_good=$(date -Iseconds)/" "$AB_STATE"
    fi
}

rollback() {
    local other
    other=$(get_inactive_slot)
    echo "Rolling back to ${other} slot..."
    set_active_slot "$other"
    echo "Rollback complete. Rebooting..."
    systemctl reboot
}

case "${1:-}" in
    activate)
        # Called at boot to check if we need rollback
        increment_boot_count
        ;;
    mark-good)
        # Called after system is verified working
        mark_good
        ;;
    switch)
        # Manual slot switch
        set_active_slot "$(get_inactive_slot)"
        echo "Switched to $(get_inactive_slot) slot. Reboot to apply."
        ;;
    rollback)
        rollback
        ;;
    status)
        echo "Active slot: $(get_active_slot)"
        echo "Inactive slot: $(get_inactive_slot)"
        [[ -f "$AB_STATE" ]] && cat "$AB_STATE"
        ;;
    *)
        echo "Usage: aion-ab-manager {activate|mark-good|switch|rollback|status}"
        ;;
esac
AB_MANAGER
    chmod +x /usr/bin/aion-ab-manager

    # ── Auto mark-good after successful boot (via systemd) ──────────
    cat > /etc/systemd/system/aion-mark-good.service <<'MARK_GOOD'
[Unit]
Description=Aion mark current boot as good
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/aion-ab-manager mark-good
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MARK_GOOD

    systemctl enable aion-ab-manager.service
    systemctl enable aion-mark-good.service

    # Enable systemd-bless-boot (auto-count failed boots)
    systemctl enable systemd-bless-boot.service

    # Initramfs with btrfs + plymouth boot splash
    sed -i 's/^MODULES=()/MODULES=(btrfs)/' /etc/mkinitcpio.conf
    sed -i 's/^BINARIES=()/BINARIES=(\/usr\/bin\/btrfs)/' /etc/mkinitcpio.conf
    sed -i 's/^HOOKS=.*/HOOKS=(base udev plymouth autodetect modconf kms keyboard keymap consolefont block filesystems fsck)/' /etc/mkinitcpio.conf
    mkinitcpio -P

    # Enable services
    systemctl enable NetworkManager.service
    systemctl enable systemd-boot-update.service

CHROOT

# ── Boot entries (written here so ${ROOT_UUID} + subvols expand) ─────
# Re-mount the active root subvolume so we can write to /boot inside it.
mount -o compress=zstd,noatime,subvol="${SUBVOL_ROOT}" "${ROOT_DEV}" /mnt 2>/dev/null || true
arch-chroot /mnt /bin/bash <<ENTRIES
mkdir -p /boot/loader/entries

cat > /boot/loader/entries/aion-active.conf <<'ENTRY'
title   Aion (Active Slot)
version aion-active
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=${ROOT_UUID} rootflags=subvol=${SUBVOL_ROOT} rw mitigations=off
ENTRY

cat > /boot/loader/entries/aion-alt.conf <<'ENTRY2'
title   Aion (Alternate Slot)
version aion-alt
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=${ROOT_UUID} rootflags=subvol=${SUBVOL_ALT} rw mitigations=off
ENTRY2
ENTRIES

# ── Initialize dual-root state ───────────────────────────────────────
mkdir -p /mnt/etc
cat > /mnt/etc/aion-ab-state <<'STATE'
active_slot=active
boot_count=0
last_good=initial
STATE

log "=== Phase 1 Complete: dual-root system with auto rollback ==="
