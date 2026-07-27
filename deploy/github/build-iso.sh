#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
ISO_NAME="nexusos"
VERSION="${1:-0.0.0-dev}"
PROFILE_DIR="$REPO_ROOT/build/iso-profile"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[build-iso]${NC} $*"; }
warn() { echo -e "${YELLOW}[build-iso]${NC} $*"; }
err() { echo -e "${RED}[build-iso]${NC} $*" >&2; exit 1; }

check_root() {
    if [ "$EUID" -ne 0 ]; then
        err "This script must be run as root (use sudo)"
    fi
}

check_deps() {
    local deps=("mkarchiso" "sha256sum" "xz" "mksquashfs" "dd" "mkfs.fat")
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &>/dev/null; then
            warn "Missing dependency: $dep"
            if command -v pacman &>/dev/null; then
                log "Install with: sudo pacman -S archiso squashfs-tools dosfstools xz"
            fi
        fi
    done
}

clean_build() {
    log "Cleaning previous build artifacts..."
    rm -rf "$BUILD_DIR/iso-profile" "$BUILD_DIR/out" "$BUILD_DIR/${ISO_NAME}-${VERSION}.iso"
    mkdir -p "$BUILD_DIR"
}

setup_profile() {
    log "Setting up ISO profile..."
    mkdir -p "$PROFILE_DIR"
    mkdir -p "$PROFILE_DIR/airootfs/etc"
    mkdir -p "$PROFILE_DIR/airootfs/etc/systemd/system"
    mkdir -p "$PROFILE_DIR/airootfs/etc/systemd/system/multi-user.target.wants"
    mkdir -p "$PROFILE_DIR/airootfs/usr/lib/nexusos"
    mkdir -p "$PROFILE_DIR/airootfs/var/lib/nexusos"
    mkdir -p "$PROFILE_DIR/airootfs/var/log/nexusos"
    mkdir -p "$PROFILE_DIR/airootfs/boot/grub"
    mkdir -p "$PROFILE_DIR/efiboot"
    mkdir -p "$PROFILE_DIR/grub"

    cat > "$PROFILE_DIR/profiledef.sh" << 'PROFILEDEF'
#!/usr/bin/env bash
iso_name="nexusos"
iso_label="NEXUSOS_$(date +%Y%m)"
iso_publisher="NexusOS <https://nexusos.dev>"
iso_application="NexusOS Immutable Linux"
iso_version="$(date +%Y.%m.%d)"
install_dir="nexusos"
bootmodes=('bios/grub' 'uefi-x64/grub')
arch="$(uname -m)"
mkarchiso_opts=()
PROFILEDEF

    log "Profile created at $PROFILE_DIR"
}

install_base_packages() {
    log "Installing base packages into ISO profile..."
    local packages=(
        base
        linux
        linux-firmware
        nano
        vim
        git
        curl
        wget
        rsync
        openssh
        networkmanager
        btrfs-progs
        dosfstools
        grub
        efibootmgr
        os-prober
        systemd-boot
        sudo
        bash-completion
        man-db
        man-pages
        python
        python-pip
        htop
        tree
        less
    )
    local pkg_str="${packages[*]}"
    sed -i "s|^packages=(|packages=($pkg_str|" "$PROFILE_DIR/packages.x86_64" 2>/dev/null || true
    echo "$pkg_str" > "$PROFILE_DIR/packages.x86_64"
}

configure_grub() {
    log "Configuring GRUB bootloader..."
    cat > "$PROFILE_DIR/grub/grub.cfg" << 'GRUBCFG'
set default=0
set timeout=5
set menu_color_normal=cyan/black
set menu_color_highlight=white/blue

insmod all_video

menuentry "NexusOS (default)" {
    linux /nexusos/boot/vmlinuz-linux root=LABEL=NEXUSOS_ROOT rw rootflags=subvol=@
    initrd /nexusos/boot/initramfs-linux.img
}

menuentry "NexusOS (fallback)" {
    linux /nexusos/boot/vmlinuz-linux root=LABEL=NEXUSOS_ROOT rw rootflags=subvol=@
    initrd /nexusos/boot/initramfs-linux-fallback.img
}

menuentry "NexusOS (snapshot: previous)" {
    linux /nexusos/boot/vmlinuz-linux root=LABEL=NEXUSOS_ROOT rw rootflags=subvol=@-rollback
    initrc /nexusos/boot/initramfs-linux.img
}

menuentry "NexusOS (live)" {
    linux /nexusos/boot/vmlinuz-linux archisobasedir=nexusos archisolabel=NEXUSOS_LIVE
    initrd /nexusos/boot/initramfs-linux.img
}
GRUBCFG

    cat > "$PROFILE_DIR/grub/grub-standalone.cfg" << 'GRUBSTANDALONE'
set default=0
set timeout=5
insmod all_video
menuentry "NexusOS" {
    linux /nexusos/boot/vmlinuz-linux root=LABEL=NEXUSOS_ROOT rw
    initrd /nexusos/boot/initramfs-linux.img
}
GRUBSTANDALONE
}

configure_systemd() {
    log "Configuring systemd services..."
    local services=(
        NetworkManager.service
        sshd.service
        systemd-timesyncd.service
    )

    for svc in "${services[@]}"; do
        if [ -f "/usr/lib/systemd/system/$svc" ]; then
            ln -sf "/usr/lib/systemd/system/$svc" \
                "$PROFILE_DIR/airootfs/etc/systemd/system/multi-user.target.wants/$svc"
        fi
    done

    cat > "$PROFILE_DIR/airootfs/etc/systemd/system/nexusos-snapshot-cleanup.service" << 'CLEANSVC'
[Unit]
Description=NexusOS Snapshot Cleanup
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/btrfs subvolume delete /var/lib/nexusos/old-snapshots/*
ExecStart=/usr/bin/btrfs subvolume delete /@-rollback-*
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
CLEANSVC

    ln -sf /usr/lib/systemd/system/nexusos-snapshot-cleanup.service \
        "$PROFILE_DIR/airootfs/etc/systemd/system/multi-user.target.wants/nexusos-snapshot-cleanup.service"
}

configure_nexusos() {
    log "Configuring NexusOS system files..."

    cat > "$PROFILE_DIR/airootfs/etc/nexusos/config.json" << NEXUSCFG
{
    "system": {
        "version": "$VERSION",
        "name": "NexusOS",
        "immutable_root": true,
        "snapshot_enabled": true,
        "ota_enabled": true,
        "rollback_enabled": true
    },
    "boot": {
        "default_subvol": "@",
        "fallback_subvol": "@-fallback",
        "rollback_subvol": "@-rollback"
    },
    "ota": {
        "manifest_url": "https://raw.githubusercontent.com/username/nexusos/main/manifest.json",
        "check_interval": 21600,
        "auto_apply": false
    }
}
NEXUSCFG

    cat > "$PROFILE_DIR/airootfs/etc/nexusos/release" << RELEASE
NexusOS $VERSION
RELEASE

    cat > "$PROFILE_DIR/airootfs/usr/lib/nexusos/update-initramfs.sh" << 'INITRAMFS'
#!/usr/bin/env bash
set -euo pipefail
mkinitcpio -P
INITRAMFS
    chmod +x "$PROFILE_DIR/airootfs/usr/lib/nexusos/update-initramfs.sh"

    cat > "$PROFILE_DIR/airootfs/usr/lib/nexusos/rollback.sh" << 'ROLLBACK'
#!/usr/bin/env bash
set -euo pipefail
SNAPSHOT="${1:-@-rollback}"
if ! btrfs subvolume show "/$SNAPSHOT" &>/dev/null; then
    echo "Snapshot $SNAPSHOT not found" >&2
    exit 1
fi
mount -o "subvol=$SNAPSHOT" /dev/disk/by-label/NEXUSOS_ROOT /mnt
grub-mkconfig -o /mnt/boot/grub/grub.cfg 2>/dev/null || true
umount /mnt
echo "Boot into $SNAPSHOT on next reboot"
ROLLBACK
    chmod +x "$PROFILE_DIR/airootfs/usr/lib/nexusos/rollback.sh"
}

build_iso() {
    log "Building ISO with mkarchiso..."
    local out_dir="$BUILD_DIR/out"
    mkdir -p "$out_dir"

    mkarchiso \
        -v \
        -w "$BUILD_DIR/work" \
        -o "$out_dir" \
        "$PROFILE_DIR"

    local iso_file
    iso_file=$(find "$out_dir" -name "*.iso" -type f | head -1)
    if [ -z "$iso_file" ]; then
        err "ISO build failed — no output file found"
    fi

    local final_iso="$BUILD_DIR/${ISO_NAME}-${VERSION}.iso"
    mv "$iso_file" "$final_iso"
    log "ISO created: $final_iso"
}

generate_checksums() {
    log "Generating SHA256 checksums..."
    local iso_file="$BUILD_DIR/${ISO_NAME}-${VERSION}.iso"
    if [ ! -f "$iso_file" ]; then
        err "ISO file not found for checksum generation"
    fi

    cd "$BUILD_DIR"
    sha256sum "${ISO_NAME}-${VERSION}.iso" > SHA256SUMS
    log "Checksums written to $BUILD_DIR/SHA256SUMS"

    local sha256
    sha256=$(awk '{print $1}' SHA256SUMS)
    log "SHA256: $sha256"
}

compress_iso() {
    log "Compressing ISO with xz..."
    local iso_file="$BUILD_DIR/${ISO_NAME}-${VERSION}.iso"
    if [ -f "$iso_file" ]; then
        xz -9 -T0 "$iso_file"
        log "Compressed: ${iso_file}.xz"
        local orig_size comp_size
        orig_size=$(stat --printf="%s" "$iso_file.xz" 2>/dev/null || stat -f%z "$iso_file.xz" 2>/dev/null || echo 0)
        comp_size=$orig_size
        log "Compressed size: $(( comp_size / 1024 / 1024 )) MB"
    fi
}

cleanup_work() {
    log "Cleaning up temporary build files..."
    rm -rf "$BUILD_DIR/work"
}

print_summary() {
    local iso_file="$BUILD_DIR/${ISO_NAME}-${VERSION}.iso.xz"
    local checksum_file="$BUILD_DIR/SHA256SUMS"

    echo ""
    echo "============================================"
    echo "  NexusOS ISO Build Complete"
    echo "============================================"
    echo "  Version: $VERSION"
    echo "  ISO:     $iso_file"
    echo "  SHA256:  $checksum_file"
    echo "============================================"
}

main() {
    log "Starting NexusOS ISO build for version $VERSION"
    check_root
    check_deps
    clean_build
    setup_profile
    install_base_packages
    configure_grub
    configure_systemd
    configure_nexusos
    build_iso
    generate_checksums
    compress_iso
    cleanup_work
    print_summary
    log "Build completed successfully"
}

main "$@"
