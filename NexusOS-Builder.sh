#!/usr/bin/env bash
# =============================================================================
# NexusOS Master Build Script
# =============================================================================
# Builds NexusOS from source into a bootable ISO image.
# Uses archiso to assemble a complete Arch Linux-based system with
# KDE Plasma, immutable Btrfs root, Waydroid, and all NexusOS components.
#
# Usage:
#   ./NexusOS-Builder.sh [VERSION]
#   ./NexusOS-Builder.sh 2.0.0
#
# Requirements:
#   - Root privileges (sudo)
#   - Internet access (package downloads)
#   - Arch Linux, Fedora, or Ubuntu host (for bootstrapping)
#   - ~10 GB free disk space
#
# Output:
#   build/nexusos-VERSION.iso      — Bootable ISO
#   build/nexusos-VERSION.iso.xz   — Compressed ISO for distribution
#   build/nexusos-VERSION.sha256   — SHA256 checksums
# =============================================================================

set -euo pipefail

# =============================================================================
# Global Constants
# =============================================================================
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
readonly VERSION="${1:-1.0.0}"
readonly BUILD_DIR="${SCRIPT_DIR}/build"
readonly ISO_NAME="nexusos-${VERSION}"
readonly ISO_LABEL="NEXUSOS_${VERSION//./_}"
readonly WORK_DIR="${BUILD_DIR}/work"
readonly OUT_DIR="${BUILD_DIR}/out"
readonly PROFILE_DIR="${WORK_DIR}/x86_64"
readonly AIROOTFS="${PROFILE_DIR}/airootfs"
readonly MIN_ISO_SIZE=$((500 * 1024 * 1024))
readonly TARGET_ARCH="x86_64"

# =============================================================================
# Color Definitions
# =============================================================================
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly MAGENTA='\033[0;35m'
readonly CYAN='\033[0;36m'
readonly WHITE='\033[1;37m'
readonly BOLD='\033[1m'
readonly NC='\033[0m'

# =============================================================================
# Output Functions
# =============================================================================
# Print an informational message to stdout.
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

# Print a success message to stdout.
log_success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

# Print a warning message to stderr.
log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

# Print an error message to stderr.
log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Print a step header for visual clarity during build.
log_step() {
    echo ""
    echo -e "${BOLD}${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${WHITE}  $*${NC}"
    echo -e "${BOLD}${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Print a banner when the script starts.
print_banner() {
    echo -e "${CYAN}"
    cat << 'BANNER'
    _   __     __  _____ _______   ____
   / | / /__  / /_/ ___// ____/ | / / /
  /  |/ / _ \/ __/\__ \/ __/ /  |/ / /
 / /|  /  __/ /_ ___/ / /___/ /|  / /
/_/ |_/\___/\__//____/_____/_/ |_/_/
BANNER
    echo -e "${NC}"
    echo -e "  ${BOLD}Version:${NC}    ${VERSION}"
    echo -e "  ${BOLD}Architecture:${NC} ${TARGET_ARCH}"
    echo -e "  ${BOLD}Build Dir:${NC}  ${BUILD_DIR}"
    echo ""
}

# =============================================================================
# Error Handling
# =============================================================================
# Global trap handler. Cleans up temporary mounts and reports failures.
on_error() {
    local exit_code=$?
    local line_number=$1
    log_error "Build failed at line ${line_number} with exit code ${exit_code}"
    cleanup_mounts
    exit "${exit_code}"
}

trap 'on_error ${LINENO}' ERR
trap 'cleanup_mounts' EXIT

# Unmount any leftover archiso overlay or work mounts.
cleanup_mounts() {
    local mount_points=("/run/archiso/bootmnt" "${WORK_DIR}/x86_64/airootfs" "${WORK_DIR}/x86_64")
    for mp in "${mount_points[@]}"; do
        if mountpoint -q "${mp}" 2>/dev/null; then
            umount -lf "${mp}" 2>/dev/null || true
        fi
    done
    if [[ -d "${WORK_DIR}/x86_64/airootfs" ]]; then
        umount -lf "${WORK_DIR}/x86_64/airootfs/dev" 2>/dev/null || true
        umount -lf "${WORK_DIR}/x86_64/airootfs/proc" 2>/dev/null || true
        umount -lf "${WORK_DIR}/x86_64/airootfs/sys" 2>/dev/null || true
    fi
}

# =============================================================================
# Dependency Management
# =============================================================================

# Detect the host Linux distribution by checking /etc/os-release.
# Returns one of: arch, fedora, ubuntu, or unknown.
detect_distro() {
    if [[ ! -f /etc/os-release ]]; then
        echo "unknown"
        return
    fi
    local distro_id
    distro_id="$(. /etc/os-release && echo "${ID:-unknown}")"
    case "${distro_id}" in
        arch|manjaro|endeavouros)
            echo "arch"
            ;;
        fedora)
            echo "fedora"
            ;;
        ubuntu|linuxmint|pop)
            echo "ubuntu"
            ;;
        *)
            echo "unknown"
            ;;
    esac
}

# Install build dependencies based on the detected host distribution.
# On Arch, installs archiso directly. On Fedora/Ubuntu, installs an
# Arch chroot environment via pacstrap or debootstrap.
install_dependencies() {
    local distro
    distro="$(detect_distro)"

    log_step "Installing build dependencies (detected: ${distro})"

    case "${distro}" in
        arch)
            install_deps_arch
            ;;
        fedora)
            install_deps_fedora
            ;;
        ubuntu)
            install_deps_ubuntu
            ;;
        *)
            log_error "Unsupported distribution. Use Arch Linux, Fedora, or Ubuntu."
            log_error "Detected: $(cat /etc/os-release 2>/dev/null || echo 'unknown')"
            exit 1
            ;;
    esac

    log_success "All dependencies installed"
}

# Install dependencies on Arch Linux (native archiso support).
install_deps_arch() {
    log_info "Installing packages via pacman..."
    pacman -Sy --needed --noconfirm \
        archiso \
        mkinitcpio \
        arch-install-scripts \
        btrfs-progs \
        dosfstools \
        e2fsprogs \
        grub \
        xz \
        gzip \
        squashfs-tools \
        imagemagick \
        gnupg \
        wget \
        base-devel \
        git

    if [[ ! -d /usr/share/archiso ]]; then
        log_error "archiso not found at /usr/share/archiso after install"
        exit 1
    fi
}

# Install dependencies on Fedora. Sets up an Arch Linux chroot for mkarchiso.
install_deps_fedora() {
    log_info "Installing packages via dnf..."
    dnf install -y \
        arch-install-scripts \
        btrfs-progs \
        dosfstools \
        e2fsprogs \
        grub2-tools \
        xz \
        gzip \
        squashfs-tools \
        ImageMagick \
        gnupg2 \
        wget \
        git \
        make \
        gcc \
        fakechroot \
        fakeroot

    ensure_archiso_available
}

# Install dependencies on Ubuntu/Debian. Sets up an Arch Linux chroot for mkarchiso.
install_deps_ubuntu() {
    log_info "Installing packages via apt..."
    apt-get update -y
    apt-get install -y \
        arch-install-scripts \
        btrfs-progs \
        dosfstools \
        e2fsprogs \
        grub-efi-amd64-bin \
        mtools \
        xz-utils \
        gzip \
        squashfs-tools \
        imagemagick \
        gnupg \
        wget \
        git \
        make \
        gcc \
        fakechroot \
        fakeroot

    ensure_archiso_available
}

# Ensure archiso scripts are available. On non-Arch hosts, clone from Git.
ensure_archiso_available() {
    if [[ -d /usr/share/archiso ]]; then
        return
    fi

    local archiso_dir="${BUILD_DIR}/archiso-git"
    if [[ ! -d "${archiso_dir}" ]]; then
        log_info "Cloning archiso from Git (non-Arch host)..."
        git clone --depth=1 https://gitlab.archlinux.org/archlinux/archiso.git "${archiso_dir}"
    fi

    # Create symlink so the rest of the script can find archiso.
    if [[ ! -d /usr/share/archiso ]]; then
        mkdir -p /usr/share/archiso
        cp -r "${archiso_dir}/configs" /usr/share/archiso/
        cp -r "${archiso_dir}/archiso" /usr/share/archiso/ 2>/dev/null || true
        cp "${archiso_dir}/archiso.sh" /usr/bin/mkarchiso 2>/dev/null || true
        chmod +x /usr/bin/mkarchiso 2>/dev/null || true
    fi
}

# =============================================================================
# Build Directory Setup
# =============================================================================

# Create the complete build directory hierarchy and clean any previous build.
setup_build_dirs() {
    log_step "Setting up build directories"

    if [[ -d "${WORK_DIR}" ]]; then
        log_warn "Previous build detected, cleaning..."
        rm -rf "${WORK_DIR}"
    fi

    mkdir -p "${BUILD_DIR}"
    mkdir -p "${WORK_DIR}"
    mkdir -p "${OUT_DIR}"
    mkdir -p "${PROFILE_DIR}"
    mkdir -p "${AIROOTFS}"

    log_success "Build directories ready at ${BUILD_DIR}"
}

# =============================================================================
# Archiso Profile Configuration
# =============================================================================

# Generate the archiso profiledef.sh that defines ISO metadata and boot config.
generate_profiledef() {
    log_info "Generating profiledef.sh..."

    cat > "${PROFILE_DIR}/profiledef.sh" << PROFILEEOF
# NexusOS archiso profile definition
iso_name="${ISO_NAME}"
iso_label="${ISO_LABEL}"
iso_publisher="NexusOS Technologies <info@nexusos.dev>"
iso_application="NexusOS ${VERSION}"
iso_version="${VERSION}"
install_dir="nexusos"
buildmodes=('iso')
bootloader=('grub')
bootargs=("quiet")
bootwait="5"
compors=("xz")
compression=("xz")
compression_options=("-9" "-T" "0")
airootfs_image_tool_options=()
PROFILEEOF

    log_success "profiledef.sh generated"
}

# Generate pacman.conf tailored for the ISO build with core/extra/community repos.
generate_pacman_conf() {
    log_info "Generating pacman.conf..."

    cat > "${PROFILE_DIR}/pacman.conf" << 'PACMANEOF'
[options]
RootDir          = /root
CacheDir         = /var/cache/pacman/pkg
GPGDir           = /etc/pacman.d/gnupg
LogFile          = /var/log/pacman.log
HoldPkg          = pacman glibc
Architecture     = x86_64
CheckSpace
SigLevel         = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 5
Color

[core]
Server = https://geo.mirror.pkgbuild.com/core/$arch
Server = https://mirror.rackspace.com/archlinux/core/$arch

[extra]
Server = https://geo.mirror.pkgbuild.com/extra/$arch
Server = https://mirror.rackspace.com/archlinux/extra/$arch

[community]
Server = https://geo.mirror.pkgbuild.com/community/$arch
Server = https://mirror.rackspace.com/archlinux/community/$arch
PACMANEOF

    log_success "pacman.conf generated"
}

# =============================================================================
# Package List
# =============================================================================

# Write the packages.x86_64 file listing all ISO packages.
generate_package_list() {
    log_info "Generating package list..."

    cat > "${PROFILE_DIR}/packages.x86_64" << 'PKGEOF'
# --- Base System ---
base
linux-zen
linux-zen-headers
firmware
linux-firmware
mkinitcpio
mkinitcpio-btrfs
grub
grub-btrfs
efibootmgr
os-prober

# --- KDE Plasma (Minimal) ---
plasma-desktop
plasma-workspace
plasma-nm
plasma-pa
plasma-systemmonitor
sddm
sddm-kcm
kde-applications
dolphin
konsole
kate
systemsettings
kde-gtk-config
oxygen-sounds
plasma-browser-integration
xdg-desktop-portal-kde
kwayland
kwin

# --- Graphics & Display ---
mesa
vulkan-icd-loader
vulkan-tools
xorg-xwayland
wayland
egl-wayland
libva-mesa-driver
mesa-vdpau

# --- Audio ---
pipewire
pipewire-alsa
pipewire-pulse
wireplumber
pavucontrol

# --- Network ---
networkmanager
network-manager-applet
iwd
wpa_supplicant
dhclient
openssh
curl
wget

# --- Filesystem ---
btrfs-progs
dosfstools
e2fsprogs
exfat-utils
ntfs-3g
udisks2

# --- Python & PyQt6 ---
python
python-pip
python-setuptools
python-wheel
python-pyqt6
python-pyqt6-webengine
python-pillow
python-evdev
python-psutil
python-requests
python-yaml
python-click
python-black

# --- Android / Waydroid ---
waydroid
lxc

# --- System Tools ---
htop
btop
neofetch
fastfetch
tree
unzip
p7zip
zip
rsync
tmux
nano
vim
micro

# --- Security ---
gpgme
nss
p11-kit
trousers

# --- Development ---
gcc
git
make
cmake
base-devel
pkg-config

# --- Fonts ---
ttf-dejavu
ttf-liberation
noto-fonts
noto-fonts-emoji
ttf-fira-code
ttf-jetbrains-mono-nerd

# --- Theming ---
breeze-icons
breeze-gtk
xcursor-breeze
kvantum

# --- Input ---
libinput
xf86-input-libinput
evdev
xf86-input-evdev

# --- Misc ---
polkit
power-profiles-daemon
thermald
baloo
packagekit
PKGEOF

    log_success "Package list generated ($(wc -l < "${PROFILE_DIR}/packages.x86_64") packages defined)"
}

# =============================================================================
# airootfs: System Configuration
# =============================================================================

# Create the base filesystem skeleton inside airootfs.
create_airootfs_skeleton() {
    log_info "Creating airootfs directory skeleton..."

    local dirs=(
        etc/nexusos
        etc/nexusos/chrome
        etc/nexusos/performance
        etc/nexusos/performance/compression
        etc/nexusos/performance/throttler
        etc/nexusos/performance/zram
        etc/selinux/nexusos
        etc/systemd/system
        etc/systemd/system/multi-user.target.wants
        etc/xdg/autostart
        usr/lib/nexusos
        usr/lib/nexusos/security
        usr/lib/nexusos/input-engine
        usr/lib/nexusos/services
        usr/lib/nexusos/oobe
        usr/lib/nexusos/wallpaper-engine
        usr/lib/nexusos/android
        usr/share/nexusos
        usr/share/nexusos/plasma
        usr/share/nexusos/icons
        var/lib/nexusos
        root
    )

    for d in "${dirs[@]}"; do
        mkdir -p "${AIROOTFS}/${d}"
    done

    log_success "airootfs skeleton created"
}

# Write the base system configuration files into airootfs.
write_system_config() {
    log_info "Writing system configuration files..."

    # Hostname
    echo "nexusos" > "${AIROOTFS}/etc/hostname"

    # Hosts
    cat > "${AIROOTFS}/etc/hosts" << 'EOF'
127.0.0.1   localhost
::1         localhost
127.0.1.1   nexusos.localdomain nexusos
EOF

    # Locale
    echo "en_US.UTF-8 UTF-8" > "${AIROOTFS}/etc/locale.gen"
    echo "LANG=en_US.UTF-8" > "${AIROOTFS}/etc/locale.conf"
    echo "KEYMAP=us" > "${AIROOTFS}/etc/vconsole.conf"

    # Timezone
    ln -sf /usr/share/zoneinfo/UTC "${AIROOTFS}/etc/localtime"

    # Shadow password — default live user (password: nexusos)
    local encrypted_pass
    encrypted_pass="$(openssl passwd -6 'nexusos')"

    cat > "${AIROOTFS}/etc/passwd" << EOF
root:x:0:0:root:/root:/bin/bash
nexusos:x:1000:1000:NexusOS User:/home/nexusos:/bin/bash
EOF

    cat > "${AIROOTFS}/etc/shadow" << EOF
root:${encrypted_pass}:19999:0:99999:7:::
nexusos:${encrypted_pass}:19999:0:99999:7:::
EOF

    cat > "${AIROOTFS}/etc/group" << 'EOF'
root:x:0:
wheel:x:10:nexusos
users:x:1000:nexusos
video:x:91:nexusos
audio:x:92:nexusos
input:x:94:nexusos
storage:x:95:nexusos
network:x:96:nexusos
power:x:97:nexusos
EOF

    # Sudoers
    mkdir -p "${AIROOTFS}/etc/sudoers.d"
    echo "nexusos ALL=(ALL:ALL) NOPASSWD: ALL" > "${AIROOTFS}/etc/sudoers.d/nexusos"
    chmod 440 "${AIROOTFS}/etc/sudoers.d/nexusos"

    # fstab for Btrfs root with subvolumes
    cat > "${AIROOTFS}/etc/fstab" << 'FSTABEOF'
# NexusOS Btrfs filesystem layout
# <device>                               <mountpoint>  <type>  <options>                                           <dump> <pass>
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /            btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@         0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /.snapshots  btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@snapshots 0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /home        btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@home    0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /var         btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@var     0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /var/cache   btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@cache   0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /boot/efi    vfat    rw,noatime,fmask=0077,dmask=0077,codepage=437,iocharset=ascii,shortname=mixed,errors=remount-ro 0  0
tmpfs                                     /tmp         tmpfs   nosuid,nodev,noatime,size=4G                            0  0
FSTABEOF

    # Tmpfiles
    cat > "${AIROOTFS}/etc/tmpfiles.d/nexusos.conf" << 'EOF'
d /var/log/nexusos 0755 nexusos nexusos -
d /var/lib/nexusos 0755 nexusos nexusos -
d /run/nexusos 0755 nexusos nexusos -
EOF

    log_success "System configuration written"
}

# =============================================================================
# airootfs: NexusOS Component Deployment
# =============================================================================

# Copy all NexusOS core components into the ISO filesystem.
deploy_core_components() {
    log_info "Deploying core components..."

    # Security module
    if [[ -d "${SCRIPT_DIR}/core/security" ]]; then
        cp -a "${SCRIPT_DIR}/core/security/"* "${AIROOTFS}/etc/selinux/nexusos/"
        mkdir -p "${AIROOTFS}/usr/lib/nexusos/security"
        cp -a "${SCRIPT_DIR}/core/security/"* "${AIROOTFS}/usr/lib/nexusos/security/"
        log_success "  core/security → /etc/selinux/nexusos/ + /usr/lib/nexusos/security/"
    else
        log_warn "  core/security/ not found, skipping"
    fi

    # Input engine
    if [[ -d "${SCRIPT_DIR}/core/input-engine" ]]; then
        cp -a "${SCRIPT_DIR}/core/input-engine/"* "${AIROOTFS}/usr/lib/nexusos/input-engine/"
        log_success "  core/input-engine → /usr/lib/nexusos/input-engine/"
    else
        log_warn "  core/input-engine/ not found, skipping"
    fi

    # Services
    if [[ -d "${SCRIPT_DIR}/core/services" ]]; then
        cp -a "${SCRIPT_DIR}/core/services/"* "${AIROOTFS}/usr/lib/nexusos/services/"
        log_success "  core/services → /usr/lib/nexusos/services/"
    else
        log_warn "  core/services/ not found, skipping"
    fi
}

# Copy all NexusOS UI components into the ISO filesystem.
deploy_ui_components() {
    log_info "Deploying UI components..."

    # Plasma config
    if [[ -d "${SCRIPT_DIR}/ui/plasma-config" ]]; then
        cp -a "${SCRIPT_DIR}/ui/plasma-config/"* "${AIROOTFS}/usr/share/nexusos/plasma/"
        log_success "  ui/plasma-config → /usr/share/nexusos/plasma/"
    else
        log_warn "  ui/plasma-config/ not found, skipping"
    fi

    # OOBE (out-of-box experience)
    if [[ -d "${SCRIPT_DIR}/ui/oobe" ]]; then
        mkdir -p "${AIROOTFS}/usr/lib/nexusos/oobe"
        find "${SCRIPT_DIR}/ui/oobe" -maxdepth 1 -type f ! -name '__pycache__' -exec cp -a {} "${AIROOTFS}/usr/lib/nexusos/oobe/" \;
        log_success "  ui/oobe → /usr/lib/nexusos/oobe/"
    else
        log_warn "  ui/oobe/ not found, skipping"
    fi

    # Wallpaper engine
    if [[ -d "${SCRIPT_DIR}/ui/wallpaper-engine" ]]; then
        cp -a "${SCRIPT_DIR}/ui/wallpaper-engine/"* "${AIROOTFS}/usr/lib/nexusos/wallpaper-engine/"
        log_success "  ui/wallpaper-engine → /usr/lib/nexusos/wallpaper-engine/"
    else
        log_warn "  ui/wallpaper-engine/ not found, skipping"
    fi

    # Icons
    if [[ -d "${SCRIPT_DIR}/ui/icons" ]]; then
        cp -a "${SCRIPT_DIR}/ui/icons/"* "${AIROOTFS}/usr/share/nexusos/icons/"
        log_success "  ui/icons → /usr/share/nexusos/icons/"
    else
        log_warn "  ui/icons/ not found, skipping"
    fi
}

# Copy Android/Waydroid integration into the ISO filesystem.
deploy_android_components() {
    log_info "Deploying Android components..."

    if [[ -d "${SCRIPT_DIR}/android" ]]; then
        cp -a "${SCRIPT_DIR}/android/"* "${AIROOTFS}/usr/lib/nexusos/android/"
        log_success "  android/* → /usr/lib/nexusos/android/"
    else
        log_warn "  android/ not found, skipping"
    fi
}

# Copy performance tuning modules into the ISO filesystem.
deploy_performance_components() {
    log_info "Deploying performance components..."

    if [[ -d "${SCRIPT_DIR}/performance" ]]; then
        cp -a "${SCRIPT_DIR}/performance/compression/"* "${AIROOTFS}/etc/nexusos/performance/compression/" 2>/dev/null || true
        cp -a "${SCRIPT_DIR}/performance/throttler/"* "${AIROOTFS}/etc/nexusos/performance/throttler/" 2>/dev/null || true
        cp -a "${SCRIPT_DIR}/performance/zram/"* "${AIROOTFS}/etc/nexusos/performance/zram/" 2>/dev/null || true
        log_success "  performance/* → /etc/nexusos/performance/"
    else
        log_warn "  performance/ not found, skipping"
    fi
}

# Copy top-level config and Chrome configuration into the ISO filesystem.
deploy_config_components() {
    log_info "Deploying config components..."

    if [[ -d "${SCRIPT_DIR}/config" ]]; then
        cp -a "${SCRIPT_DIR}/config/"* "${AIROOTFS}/etc/nexusos/" 2>/dev/null || true
        log_success "  config/* → /etc/nexusos/"
    else
        log_warn "  config/ not found, skipping"
    fi

    if [[ -d "${SCRIPT_DIR}/chrome" ]]; then
        cp -a "${SCRIPT_DIR}/chrome/"* "${AIROOTFS}/etc/nexusos/chrome/" 2>/dev/null || true
        log_success "  chrome/* → /etc/nexusos/chrome/"
    else
        log_warn "  chrome/ not found, skipping"
    fi
}

# Run all component deployment steps in order.
deploy_all_components() {
    log_step "Deploying NexusOS components into ISO filesystem"
    deploy_core_components
    deploy_ui_components
    deploy_android_components
    deploy_performance_components
    deploy_config_components
    log_success "All components deployed"
}

# =============================================================================
# airootfs: systemd Services
# =============================================================================

# Enable systemd services in the ISO by creating symlinks in multi-user.target.wants.
enable_systemd_services() {
    log_step "Configuring systemd services"

    local services_wants="${AIROOTFS}/etc/systemd/system/multi-user.target.wants"
    mkdir -p "${services_wants}"

    local service_files=(
        "nexusos-init.service"
        "nexusos-security.service"
        "nexusos-input.service"
        "nexusos-oobe.service"
        "nexusos-throttler.service"
    )

    for svc in "${service_files[@]}"; do
        local found=0
        # Search all source directories for this service file
        for search_dir in "${SCRIPT_DIR}/core" "${SCRIPT_DIR}/ui" "${SCRIPT_DIR}/performance"; do
            local svc_path
            svc_path="$(find "${search_dir}" -name "${svc}" -type f 2>/dev/null | head -n1)"
            if [[ -n "${svc_path}" ]]; then
                cp -a "${svc_path}" "${AIROOTFS}/etc/systemd/system/${svc}"
                ln -sf "/etc/systemd/system/${svc}" "${services_wants}/${svc}"
                log_info "  Enabled: ${svc}"
                found=1
                break
            fi
        done

        if [[ "${found}" -eq 0 ]]; then
            log_warn "  Service not found: ${svc}"
        fi
    done

    # Create a placeholder for the NexusOS boot service
    cat > "${AIROOTFS}/etc/systemd/system/nexusos-boot-setup.service" << 'BOOTEOF'
[Unit]
Description=NexusOS Boot Setup - Configures immutable Btrfs root
After=local-fs.target
Before=graphical.target
ConditionPathExists=!/var/lib/nexusos/.boot-configured

[Service]
Type=oneshot
ExecStart=/usr/lib/nexusos/services/immount-root.sh
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
BOOTEOF
    ln -sf /etc/systemd/system/nexusos-boot-setup.service \
        "${services_wants}/nexusos-boot-setup.service"

    log_success "Systemd services configured"
}

# =============================================================================
# airootfs: Btrfs Immutable Root Setup
# =============================================================================

# Install the immutable Btrfs root initialization script that runs on first boot.
setup_immutable_root() {
    log_step "Setting up immutable Btrfs root"

    cat > "${AIROOTFS}/usr/lib/nexusos/services/immount-root.sh" << 'IMROOT'
#!/usr/bin/env bash
# NexusOS Immutable Btrfs Root Setup
# Runs once on first boot to configure Btrfs subvolume layout.
# This script is idempotent — safe to re-run.

set -euo pipefail

LOG_FILE="/var/log/nexusos/boot-setup.log"
mkdir -p "$(dirname "${LOG_FILE}")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "NexusOS immutable root setup starting..."

# Detect root device
ROOT_DEV="$(findmnt -n -o SOURCE /)"
if [[ -z "${ROOT_DEV}" ]]; then
    log "ERROR: Cannot detect root device"
    exit 1
fi

# Check if already configured
if [[ -f /var/lib/nexusos/.boot-configured ]]; then
    log "Immutable root already configured, skipping"
    exit 0
fi

# Set Btrfs read-only flag on root subvolume if not already set
if command -v btrfs &>/dev/null; then
    ROOT_SUBVOL="$(btrfs subvol list / 2>/dev/null | head -n1 | awk '{print $NF}')"
    if [[ -n "${ROOT_SUBVOL}" ]]; then
        log "Root subvolume: ${ROOT_SUBVOL}"
    fi
fi

# Configure zram if available
if [[ -f /etc/nexusos/performance/zram/zram-generator.conf ]]; then
    cp /etc/nexusos/performance/zram/zram-generator.conf /etc/zram-generator.conf 2>/dev/null || true
    log "zram configuration applied"
fi

# Configure Btrfs compression hints
if [[ -f /etc/nexusos/performance/compression/btrfs-compression.sh ]]; then
    chmod +x /etc/nexusos/performance/compression/btrfs-compression.sh
    log "Btrfs compression script installed"
fi

# Mark as configured
touch /var/lib/nexusos/.boot-configured
log "NexusOS immutable root setup complete"

exit 0
IMROOT
    chmod +x "${AIROOTFS}/usr/lib/nexusos/services/immount-root.sh"

    log_success "Immutable Btrfs root setup script installed"
}

# =============================================================================
# airootfs: User Environment
# =============================================================================

# Set up the live user environment including directories, default configs,
# and the first-boot OOBE trigger.
setup_user_environment() {
    log_step "Configuring user environment"

    # Home directory structure
    mkdir -p "${AIROOTFS}/home/nexusos"
    mkdir -p "${AIROOTFS}/home/nexusos/Desktop"
    mkdir -p "${AIROOTFS}/home/nexusos/Documents"
    mkdir -p "${AIROOTFS}/home/nexusos/Downloads"
    mkdir -p "${AIROOTFS}/home/nexusos/.config"
    mkdir -p "${AIROOTFS}/home/nexusos/.local/share"
    mkdir -p "${AIROOTFS}/home/nexusos/.local/share/applications"

    # Set ownership (uid 1000 = nexusos user)
    chown -hR 1000:1000 "${AIROOTFS}/home/nexusos" 2>/dev/null || true

    # Shell profile
    cat > "${AIROOTFS}/home/nexusos/.bash_profile" << 'BASHPROF'
# NexusOS user profile
export XDG_CURRENT_DESKTOP=KDE
export XDG_SESSION_DESKTOP=KDE
export XDG_SESSION_TYPE=wayland
export QT_QPA_PLATFORM=wayland
export MOZ_ENABLE_WAYLAND=1
export NEXUS_HOME=/usr/lib/nexusos
export NEXUS_CONFIG=/etc/nexusos

# Launch SDDM on tty1 login
if [[ -z "${DISPLAY}" && "${XDG_VTNR}" -eq 1 ]]; then
    exec startplasma-wayland
fi
BASHPROF
    chown 1000:1000 "${AIROOTFS}/home/nexusos/.bash_profile" 2>/dev/null || true

    # KDE autostart for OOBE
    cat > "${AIROOTFS}/etc/xdg/autostart/nexusos-oobe.desktop" << 'OOBEOF'
[Desktop Entry]
Type=Application
Name=NexusOS Setup Wizard
Comment=NexusOS first-boot configuration wizard
Exec=/usr/lib/nexusos/oobe/oobe_wizard.py
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after-panel=true
OOBEOF

    # SDDM configuration
    mkdir -p "${AIROOTFS}/etc/sddm.conf.d"
    cat > "${AIROOTFS}/etc/sddm.conf.d/nexusos.conf" << 'SDDMEOF'
[Autologin]
Relogin=false

[General]
DisplayServer=wayland
GreeterEnvironment=QT_WAYLAND_DISABLE_WINDOWDECORATION=1

[Wayland]
CompositorCommand=kwin_wayland --drm --no-lockscreen --no-global-shortcuts --locale1 --inputmethod maliit-keyboard
SDDMEOF

    log_success "User environment configured"
}

# =============================================================================
# airootfs: GRUB Configuration
# =============================================================================

# Install GRUB bootloader configuration for the ISO.
configure_grub() {
    log_step "Configuring GRUB bootloader"

    mkdir -p "${AIROOTFS}/etc/default"

    cat > "${AIROOTFS}/etc/default/grub" << 'GRUBEOF'
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR="NexusOS"
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""
GRUB_TERMINAL_INPUT="console"
GRUB_TERMINAL_OUTPUT="console"
GRUB_GFXMODE=auto
GRUB_GFXPAYLOAD_LINUX=keep
GRUB_DISABLE_OS_PROBER=false
GRUBEOF

    # GRUB boot menu entries for immutable Btrfs root
    mkdir -p "${AIROOTFS}/boot/grub"
    mkdir -p "${AIROOTFS}/boot/grub/themes"

    cat > "${AIROOTFS}/boot/grub/grub.cfg" << 'GRUBCFG'
set default=0
set timeout=5
set gfxmode=auto

loadfont unicode

set menu_color_normal=cyan/black
set menu_color_highlight=white/blue

menuentry "NexusOS ${VERSION}" --class nexusos --class gnu-linux --class os {
    search --no-floppy --fs-uuid --set=root XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
    linux /boot/vmlinuz-linux-zen root=UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX rootflags=subvol=/@ rw quiet splash
    initrd /boot/initramfs-linux-zen.img
}

menuentry "NexusOS ${VERSION} (Recovery)" --class nexusos --class gnu-linux --class os {
    search --no-floppy --fs-uuid --set=root XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
    linux /boot/vmlinuz-linux-zen root=UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX rootflags=subvol=/@ rw single
    initrd /boot/initramfs-linux-zen.img
}

menuentry "UEFI Firmware Settings" --class firmware {
    fwsetup
}
GRUBCFG

    log_success "GRUB configured"
}

# =============================================================================
# airootfs: mkinitcpio Configuration
# =============================================================================

# Write mkinitcpio.conf with Btrfs and essential modules for boot.
configure_mkinitcpio() {
    log_info "Configuring mkinitcpio..."

    cat > "${AIROOTFS}/etc/mkinitcpio.conf" << 'MKINIEOF'
MODULES=(btrfs amdgpu radeon i915)
BINARIES=()
FILES=()
HOOKS=(base udev autodetect modconf kms keyboard keymap consolefont block filesystems fsck)
COMPRESSION="zstd"
COMPRESSION_OPTIONS=(-9)
MKINIEOF

    log_success "mkinitcpio.conf configured"
}

# =============================================================================
# ISO Generation
# =============================================================================

# Run mkarchiso to build the actual ISO image from the assembled profile.
generate_iso() {
    log_step "Generating ISO image"

    local iso_output="${OUT_DIR}/${ISO_NAME}.iso"
    mkdir -p "${OUT_DIR}"

    if command -v mkarchiso &>/dev/null; then
        log_info "Running mkarchiso..."
        mkarchiso -v -w "${WORK_DIR}" -o "${OUT_DIR}" "${PROFILE_DIR}"
    elif [[ -x /usr/share/archiso/archiso.sh ]]; then
        log_info "Running archiso.sh directly..."
        /usr/share/archiso/archiso.sh -v -w "${WORK_DIR}" -o "${OUT_DIR}" "${PROFILE_DIR}"
    elif [[ -f "${BUILD_DIR}/archiso-git/archiso.sh" ]]; then
        log_info "Running cloned archiso.sh..."
        bash "${BUILD_DIR}/archiso-git/archiso.sh" -v -w "${WORK_DIR}" -o "${OUT_DIR}" "${PROFILE_DIR}"
    else
        log_error "mkarchiso not found. Install archiso or ensure it was cloned."
        exit 1
    fi

    # mkarchiso outputs to ${OUT_DIR}/ with its own naming — find and rename
    local generated_iso
    generated_iso="$(find "${OUT_DIR}" -name "*.iso" -newer "${PROFILE_DIR}/profiledef.sh" -type f | head -n1)"

    if [[ -z "${generated_iso}" ]]; then
        log_error "ISO file not found after mkarchiso completed"
        ls -la "${OUT_DIR}/"
        exit 1
    fi

    if [[ "${generated_iso}" != "${iso_output}" ]]; then
        mv "${generated_iso}" "${iso_output}"
    fi

    log_success "ISO generated: ${iso_output}"
}

# =============================================================================
# Post-Build: Validation, Checksums, Compression
# =============================================================================

# Validate the ISO was created successfully and meets minimum size requirements.
validate_iso() {
    local iso_path="${OUT_DIR}/${ISO_NAME}.iso"

    log_step "Validating ISO image"

    if [[ ! -f "${iso_path}" ]]; then
        log_error "ISO file does not exist: ${iso_path}"
        exit 1
    fi

    local iso_size
    iso_size="$(stat -c%s "${iso_path}" 2>/dev/null || stat -f%z "${iso_path}" 2>/dev/null)"

    if [[ "${iso_size}" -lt "${MIN_ISO_SIZE}" ]]; then
        local size_mb=$((iso_size / 1024 / 1024))
        log_error "ISO too small: ${size_mb}MB (minimum: 500MB). Build likely incomplete."
        exit 1
    fi

    local size_mb=$((iso_size / 1024 / 1024))
    log_success "ISO validated: ${size_mb}MB"

    # Verify it is a valid ISO 9660 filesystem
    if command -v file &>/dev/null; then
        local file_type
        file_type="$(file -b "${iso_path}" 2>/dev/null || true)"
        if echo "${file_type}" | grep -qi "iso 9660\|boot sector"; then
            log_success "ISO format verified: ${file_type}"
        else
            log_warn "ISO format check inconclusive: ${file_type}"
        fi
    fi
}

# Generate SHA256 checksums for the ISO and compressed ISO.
generate_checksums() {
    local checksum_file="${OUT_DIR}/${ISO_NAME}.sha256"

    log_step "Generating SHA256 checksums"

    cd "${OUT_DIR}"
    sha256sum "${ISO_NAME}.iso" > "${checksum_file}"
    if [[ -f "${ISO_NAME}.iso.xz" ]]; then
        sha256sum "${ISO_NAME}.iso.xz" >> "${checksum_file}"
    fi
    cd "${SCRIPT_DIR}"

    log_success "Checksums written: ${checksum_file}"
    cat "${checksum_file}"
}

# Compress the ISO with xz for distribution. Uses maximum compression.
compress_iso() {
    local iso_path="${OUT_DIR}/${ISO_NAME}.iso"
    local xz_path="${iso_path}.xz"

    log_step "Compressing ISO with xz (this may take a while)"

    if [[ -f "${xz_path}" ]]; then
        rm -f "${xz_path}"
    fi

    xz -9 -T 0 "${iso_path}"
    log_success "Compressed: ${xz_path}"

    local xz_size
    xz_size="$(stat -c%s "${xz_path}" 2>/dev/null || stat -f%z "${xz_path}" 2>/dev/null)"
    local xz_mb=$((xz_size / 1024 / 1024))
    log_success "Compressed size: ${xz_mb}MB"
}

# =============================================================================
# Build Report
# =============================================================================

# Print a summary of the completed build.
print_build_report() {
    local iso_path="${OUT_DIR}/${ISO_NAME}.iso"
    local xz_path="${iso_path}.xz"
    local sha_path="${OUT_DIR}/${ISO_NAME}.sha256"

    echo ""
    echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${WHITE}  BUILD COMPLETE — NexusOS ${VERSION}${NC}"
    echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    if [[ -f "${iso_path}" ]]; then
        local iso_size
        iso_size="$(stat -c%s "${iso_path}" 2>/dev/null || stat -f%z "${iso_path}" 2>/dev/null)"
        echo -e "  ${BOLD}ISO:${NC}     ${iso_path} ($(( iso_size / 1024 / 1024 ))MB)"
    fi

    if [[ -f "${xz_path}" ]]; then
        local xz_size
        xz_size="$(stat -c%s "${xz_path}" 2>/dev/null || stat -f%z "${xz_path}" 2>/dev/null)"
        echo -e "  ${BOLD}Compressed:${NC} ${xz_path} ($(( xz_size / 1024 / 1024 ))MB)"
    fi

    if [[ -f "${sha_path}" ]]; then
        echo -e "  ${BOLD}Checksums:${NC} ${sha_path}"
    fi

    echo ""
    echo -e "  ${CYAN}Write to USB:${NC}  sudo dd if=${xz_path} of=/dev/sdX bs=4M status=progress oflag=sync"
    echo -e "  ${CYAN}Verify USB:${NC}    sudo cmp ${xz_path} /dev/sdX"
    echo ""
    echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# =============================================================================
# Prerequisite Checks
# =============================================================================

# Verify the script is running with sufficient privileges and in the right location.
preflight_checks() {
    log_step "Running preflight checks"

    # Must be root for archiso and package installation
    if [[ "${EUID}" -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
    log_success "Running as root"

    # Verify we are in the NexusOS project root
    if [[ ! -f "${SCRIPT_DIR}/core/security/nexusos-security.service" ]]; then
        log_error "Cannot find NexusOS project files. Run this script from the NexusOS root directory."
        log_error "Expected: ${SCRIPT_DIR}/core/security/nexusos-security.service"
        exit 1
    fi
    log_success "NexusOS project root verified"

    # Verify required tools
    local required_tools=("mkarchiso" "btrfs-progs" "xz" "openssl")
    for tool in "${required_tools[@]}"; do
        if ! command -v "${tool}" &>/dev/null; then
            # btrfs-progs provides mkfs.btrfs, not 'btrfs-progs' as a command
            if [[ "${tool}" == "btrfs-progs" ]]; then
                if ! command -v mkfs.btrfs &>/dev/null; then
                    log_error "Missing required tool: mkfs.btrfs (install btrfs-progs)"
                    exit 1
                fi
            else
                log_error "Missing required tool: ${tool}"
                exit 1
            fi
        fi
    done
    log_success "All required tools available"

    # Check disk space (need ~10GB)
    local free_space_kb
    free_space_kb="$(df -P "${BUILD_DIR}" 2>/dev/null | awk 'NR==2{print $4}' || echo 0)"
    if [[ "${free_space_kb}" -lt 10485760 ]]; then
        local free_gb=$((free_space_kb / 1024 / 1024))
        log_warn "Low disk space: ${free_gb}GB free (recommend 10GB+)"
    fi

    log_success "Preflight checks passed"
}

# =============================================================================
# Main Entry Point
# =============================================================================

# Orchestrates the entire NexusOS build process from dependency installation
# through ISO generation, validation, and compression.
main() {
    print_banner
    preflight_checks
    install_dependencies
    setup_build_dirs
    generate_profiledef
    generate_pacman_conf
    generate_package_list
    create_airootfs_skeleton
    write_system_config
    configure_mkinitcpio
    configure_grub
    setup_user_environment
    setup_immutable_root
    deploy_all_components
    enable_systemd_services
    generate_iso
    validate_iso
    compress_iso
    generate_checksums
    print_build_report
}

main "$@"
