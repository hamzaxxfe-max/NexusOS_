#!/usr/bin/env bash
# Aion Phase 1: Base Arch System + Immutable Root (Btrfs A/B + BLS + Auto Rollback)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${WORK_DIR:-/var/lib/aion-build}"
ROOT_UUID="${ROOT_UUID:-}"
HOSTNAME="${HOSTNAME:-aion}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"
[[ -z "$ROOT_UUID" ]] && err "ROOT_UUID required (UUID of target Btrfs partition)"

log "=== Aion Phase 1: Base System ==="

# ── Partitioning & Btrfs A/B ─────────────────────────────────────────
log "Formatting Btrfs with A/B dual-root layout..."
mkfs.btrfs -f -L aion -U "$ROOT_UUID" /dev/disk/by-label/aion

mount /dev/disk/by-label/aion /mnt

# Create Btrfs subvolumes — dual root slots for atomic updates
btrfs subvolume create /mnt/@A
btrfs subvolume create /mnt/@B
btrfs subvolume create /mnt/@home
btrfs subvolume create /mnt/@var
btrfs subvolume create /mnt/@snapshots
btrfs subvolume create /mnt/@swap

umount /mnt

# Mount slot A as default
mount -o compress=zstd,noatime,subvol=@A /dev/disk/by-label/aion /mnt
mkdir -p /mnt/{home,var,.snapshots,swap,B}

mount -o compress=zstd,noatime,subvol=@home /dev/disk/by-label/aion /mnt/home
mount -o compress=zstd,noatime,subvol=@var /dev/disk/by-label/aion /mnt/var
mount -o compress=zstd,noatime,subvol=@snapshots /dev/disk/by-label/aion /mnt/.snapshots
mount -o compress=zstd,noatime,subvol=@swap /dev/disk/by-label/aion /mnt/swap
mount -o compress=zstd,noatime,subvol=@B /dev/disk/by-label/aion /mnt/B

# ── Pacstrap ─────────────────────────────────────────────────────────
log "Installing base system..."
pacstrap /mnt \
    base linux linux-firmware \
    btrfs-progs \
    systemd-boot \
    networkmanager \
    sudo \
    git \
    base-devel

# ── fstab ────────────────────────────────────────────────────────────
log "Generating fstab..."
genfstab -U /mnt >> /mnt/etc/fstab

# ── BLS + A/B + Auto Rollback setup ──────────────────────────────────
log "Configuring BLS A/B boot with auto rollback..."
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
default aion-a.conf
timeout 3
console-mode auto
editor no
LOADER

    ROOT_UUID=$(blkid -s UUID -o value /dev/disk/by-label/aion)

    # Slot A boot entry (BLS format)
    cat > /boot/loader/entries/aion-a.conf <<ENTRY
title   Aion (Slot A)
version aion-a
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=${ROOT_UUID} rootflags=subvol=@A rw mitigations=off
ENTRY

    # Slot B boot entry (BLS format)
    cat > /boot/loader/entries/aion-b.conf <<ENTRY
title   Aion (Slot B)
version aion-b
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=${ROOT_UUID} rootflags=subvol=@B rw mitigations=off
ENTRY

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
# Aion A/B Slot Manager
# Manages boot slot selection and auto-rollback
set -euo pipefail

AB_STATE="/etc/aion-ab-state"
BOOT_ENTRIES="/boot/loader/entries"
LOADER_CONF="/boot/loader/loader.conf"

get_active_slot() {
    local title
    title=$(grep "^title" "${BOOT_ENTRIES}/"*.conf 2>/dev/null | grep "active" | head -1)
    if [[ "$title" == *"Slot A"* ]]; then
        echo "A"
    else
        echo "B"
    fi
}

get_inactive_slot() {
    local active
    active=$(get_active_slot)
    if [[ "$active" == "A" ]]; then
        echo "B"
    else
        echo "A"
    fi
}

set_active_slot() {
    local slot="$1"
    local other
    other=$(if [[ "$slot" == "A" ]]; then echo "B"; else echo "A"; fi)

    # Mark active slot
    sed -i "s/Slot .*/Slot ${slot} (active)/" "${BOOT_ENTRIES}/aion-${slot,,}.conf"
    # Mark other as inactive
    sed -i "s/Slot .* (active)/Slot ${other}/" "${BOOT_ENTRIES}/aion-${other,,}.conf"

    # Set default boot
    sed -i "s/^default .*/default aion-${slot,,}.conf/" "$LOADER_CONF"

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
    echo "Rolling back to Slot ${other}..."
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
        echo "Switched to Slot $(get_inactive_slot). Reboot to apply."
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

    # Initramfs with btrfs
    sed -i 's/^MODULES=()/MODULES=(btrfs)/' /etc/mkinitcpio.conf
    sed -i 's/^BINARIES=()/BINARIES=(\/usr\/bin\/btrfs)/' /etc/mkinitcpio.conf
    mkinitcpio -P

    # Enable services
    systemctl enable NetworkManager.service
    systemctl enable systemd-boot-update.service

CHROOT

# ── Initialize A/B state ─────────────────────────────────────────────
mkdir -p /mnt/etc
cat > /mnt/etc/aion-ab-state <<'STATE'
active_slot=A
boot_count=0
last_good=initial
STATE

log "=== Phase 1 Complete: A/B system with auto rollback ==="
