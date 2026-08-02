#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
ISO_NAME="aion"
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
    mkdir -p "$PROFILE_DIR/airootfs/usr/lib/aion"
    mkdir -p "$PROFILE_DIR/airootfs/var/lib/aion"
    mkdir -p "$PROFILE_DIR/airootfs/var/log/aion"
    mkdir -p "$PROFILE_DIR/airootfs/boot/grub"
    mkdir -p "$PROFILE_DIR/efiboot"
    mkdir -p "$PROFILE_DIR/grub"

    cat > "$PROFILE_DIR/profiledef.sh" << 'PROFILEDEF'
#!/usr/bin/env bash
iso_name="aion"
iso_label="AION_$(date +%Y%m)"
iso_publisher="Aion <https://aion.dev>"
iso_application="Aion Immutable Linux"
iso_version="$(date +%Y.%m.%d)"
install_dir="aion"
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
set timeout=3

serial --speed=115200 --unit=0 --word=8 --parity=no --stop=1
terminal_input serial console
terminal_output serial console

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

menuentry "Aion (default)" {
    linux /aion/boot/vmlinuz-linux root=LABEL=AION_ROOT rw rootflags=subvol=@ console=ttyS0,115200n8
    initrd /aion/boot/initramfs-linux.img
}

menuentry "Aion (fallback)" {
    linux /aion/boot/vmlinuz-linux root=LABEL=AION_ROOT rw rootflags=subvol=@ console=ttyS0,115200n8
    initrd /aion/boot/initramfs-linux-fallback.img
}

menuentry "Aion (snapshot: previous)" {
    linux /aion/boot/vmlinuz-linux root=LABEL=AION_ROOT rw rootflags=subvol=@-rollback console=ttyS0,115200n8
    initrd /aion/boot/initramfs-linux.img
}

menuentry "Aion (live)" {
    linux /aion/boot/vmlinuz-linux archisobasedir=aion archisolabel=AION_LIVE console=ttyS0,115200n8
    initrd /aion/boot/initramfs-linux.img
}
GRUBCFG

    cat > "$PROFILE_DIR/grub/grub-standalone.cfg" << 'GRUBSTANDALONE'
set default=0
set timeout=3

serial --speed=115200 --unit=0 --word=8 --parity=no --stop=1
terminal_input serial console
terminal_output serial console

menuentry "Aion" {
    linux /aion/boot/vmlinuz-linux root=LABEL=AION_ROOT rw console=ttyS0,115200n8
    initrd /aion/boot/initramfs-linux.img
}
GRUBSTANDALONE
}

configure_syslinux() {
    log "Configuring Syslinux/Isolinux for BIOS boot..."
    local syslinux_dir="$PROFILE_DIR/syslinux"
    mkdir -p "$syslinux_dir"

    cat > "$syslinux_dir/syslinux.cfg" << 'SYSLINUXCFG'
PROMPT 0
TIMEOUT 30
DEFAULT aion

UI /boot/syslinux/vesamenu.c32

MENU TITLE Aion Boot Menu
MENU COLOR title        1;36;44
MENU COLOR sel          7;37;40
MENU COLOR unsel        37;44

LABEL aion
    MENU LABEL Aion (default)
    LINUX /boot/vmlinuz-linux
    INITRD /boot/initramfs-linux.img
    APPEND root=LABEL=AION_ROOT rw rootflags=subvol=@ quiet splash

LABEL aion-fallback
    MENU LABEL Aion (fallback)
    LINUX /boot/vmlinuz-linux
    INITRD /boot/initramfs-linux-fallback.img
    APPEND root=LABEL=AION_ROOT rw rootflags=subvol=@

LABEL reboot
    MENU LABEL Reboot
    COM32 chain.c32
    APPEND reboot

LABEL poweroff
    MENU LABEL Power Off
    COM32 chain.c32
    APPEND poweroff
SYSLINUXCFG

    log "Syslinux configured"
}

patch_boot_timeout() {
    log "Patching boot timeout values (safety net)..."
    local patched=0

    for grub_cfg in "$PROFILE_DIR/grub/grub.cfg" "$PROFILE_DIR/grub/grub-standalone.cfg"; do
        if [ -f "$grub_cfg" ]; then
            if grep -q "^set timeout=" "$grub_cfg" 2>/dev/null; then
                sed -i 's/^set timeout=[0-9]*/set timeout=3/' "$grub_cfg"
                patched=$((patched + 1))
            fi
        fi
    done

    local syslinux_cfg="$PROFILE_DIR/syslinux/syslinux.cfg"
    if [ -f "$syslinux_cfg" ]; then
        if grep -q "^TIMEOUT " "$syslinux_cfg" 2>/dev/null; then
            sed -i 's/^TIMEOUT [0-9]*/TIMEOUT 30/' "$syslinux_cfg"
            patched=$((patched + 1))
        fi
    fi

    log "Boot timeout patched in $patched file(s)"
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

    cat > "$PROFILE_DIR/airootfs/etc/systemd/system/aion-snapshot-cleanup.service" << 'CLEANSVC'
[Unit]
Description=Aion Snapshot Cleanup
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/btrfs subvolume delete /var/lib/aion/old-snapshots/*
ExecStart=/usr/bin/btrfs subvolume delete /@-rollback-*
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
CLEANSVC

    ln -sf /usr/lib/systemd/system/aion-snapshot-cleanup.service \
        "$PROFILE_DIR/airootfs/etc/systemd/system/multi-user.target.wants/aion-snapshot-cleanup.service"
}

configure_aion() {
    log "Configuring Aion system files..."

    # Install the Aion OTA release public key for GPG signature verification.
    # ota-updater.py verifies every payload signature against this keyring
    # BEFORE checksum verification and BEFORE mounting the target subvolume.
    if [ -f "${SCRIPT_DIR}/../ota/aion-release.asc" ]; then
        mkdir -p "$PROFILE_DIR/airootfs/etc/aion/gpg"
        cp "${SCRIPT_DIR}/../ota/aion-release.asc" \
            "$PROFILE_DIR/airootfs/etc/aion/gpg/aion-release.asc"
        chmod 644 "$PROFILE_DIR/airootfs/etc/aion/gpg/aion-release.asc"
        log "Installed OTA release public key to /etc/aion/gpg/aion-release.asc"
    else
        log "WARN: deploy/ota/aion-release.asc not found — OTA signatures cannot be verified"
    fi

    cat > "$PROFILE_DIR/airootfs/etc/aion/config.json" << NEXUSCFG
{
    "system": {
        "version": "$VERSION",
        "name": "Aion",
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
        "manifest_url": "https://raw.githubusercontent.com/username/aion/main/manifest.json",
        "check_interval": 21600,
        "auto_apply": false
    }
}
NEXUSCFG

    cat > "$PROFILE_DIR/airootfs/etc/aion/release" << RELEASE
Aion $VERSION
RELEASE

    cat > "$PROFILE_DIR/airootfs/usr/lib/aion/update-initramfs.sh" << 'INITRAMFS'
#!/usr/bin/env bash
set -euo pipefail
mkinitcpio -P
INITRAMFS
    chmod +x "$PROFILE_DIR/airootfs/usr/lib/aion/update-initramfs.sh"

    cat > "$PROFILE_DIR/airootfs/usr/lib/aion/rollback.sh" << 'ROLLBACK'
#!/usr/bin/env bash
set -euo pipefail
SNAPSHOT="${1:-@-rollback}"
if ! btrfs subvolume show "/$SNAPSHOT" &>/dev/null; then
    echo "Snapshot $SNAPSHOT not found" >&2
    exit 1
fi
mount -o "subvol=$SNAPSHOT" /dev/disk/by-label/AION_ROOT /mnt
grub-mkconfig -o /mnt/boot/grub/grub.cfg 2>/dev/null || true
umount /mnt
echo "Boot into $SNAPSHOT on next reboot"
ROLLBACK
    chmod +x "$PROFILE_DIR/airootfs/usr/lib/aion/rollback.sh"
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
    echo "  Aion ISO Build Complete"
    echo "============================================"
    echo "  Version: $VERSION"
    echo "  ISO:     $iso_file"
    echo "  SHA256:  $checksum_file"
    echo "============================================"
}

main() {
    log "Starting Aion ISO build for version $VERSION"
    check_root
    check_deps
    clean_build
    setup_profile
    install_base_packages
    configure_grub
    configure_syslinux
    patch_boot_timeout
    configure_systemd
    configure_aion
    build_iso
    generate_checksums
    compress_iso
    cleanup_work
    print_summary
    log "Build completed successfully"
}

main "$@"
