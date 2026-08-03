#!/usr/bin/env bash
# =============================================================================
# Aion Master Build Script
# =============================================================================
# Builds Aion from source into a bootable ISO image.
# Uses archiso to assemble a complete Arch Linux-based system with
# KDE Plasma, immutable Btrfs root, Waydroid, and all Aion components.
#
# Usage:
#   ./Aion-Builder.sh [VERSION]
#   ./Aion-Builder.sh 2.0.0
#
# Requirements:
#   - Root privileges (sudo)
#   - Internet access (package downloads)
#   - Arch Linux, Fedora, or Ubuntu host (for bootstrapping)
#   - ~10 GB free disk space
#
# Output:
#   build/aion-VERSION.iso      — Bootable ISO
#   build/aion-VERSION.iso.xz   — Compressed ISO for distribution
#   build/aion-VERSION.sha256   — SHA256 checksums
# =============================================================================

set -euo pipefail

# =============================================================================
# Global Constants
# =============================================================================
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
readonly VERSION="${1:-1.0.0}"
readonly BUILD_DIR="${SCRIPT_DIR}/build"
readonly ISO_NAME="aion-${VERSION}"
readonly ISO_LABEL="AION_${VERSION//./_}"
readonly WORK_DIR="${BUILD_DIR}/work"
readonly OUT_DIR="${BUILD_DIR}/out"
readonly PROFILE_DIR="${BUILD_DIR}/profile"
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
    distro_id="$(grep -oP '^ID=\K.*' /etc/os-release 2>/dev/null | tr -d '"' || echo "unknown")"
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
        mtools \
        libisoburn \
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
    if command -v mkarchiso &>/dev/null; then
        return
    fi

    local archiso_dir="${BUILD_DIR}/archiso-git"
    if [[ ! -d "${archiso_dir}" ]]; then
        log_info "Cloning archiso from Git (non-Arch host)..."
        git clone --depth=1 https://gitlab.archlinux.org/archlinux/archiso.git "${archiso_dir}"
    fi

    # Modern archiso: the main script is at archiso/mkarchiso
    if [[ -f "${archiso_dir}/archiso/mkarchiso" ]]; then
        cp "${archiso_dir}/archiso/mkarchiso" /usr/local/bin/mkarchiso
        chmod +x /usr/local/bin/mkarchiso
        log_success "mkarchiso installed at /usr/local/bin/mkarchiso"
    elif [[ -f "${archiso_dir}/archiso.sh" ]]; then
        # Legacy archiso (old layout)
        cp "${archiso_dir}/archiso.sh" /usr/local/bin/mkarchiso
        chmod +x /usr/local/bin/mkarchiso
        log_success "mkarchiso installed (legacy) at /usr/local/bin/mkarchiso"
    else
        log_error "Cannot find mkarchiso in cloned archiso repository"
        log_error "Contents of ${archiso_dir}:"
        ls -la "${archiso_dir}/"
        exit 1
    fi

    # Also copy configs for mkarchiso to find
    mkdir -p /usr/share/archiso
    if [[ -d "${archiso_dir}/configs" ]]; then
        cp -r "${archiso_dir}/configs" /usr/share/archiso/
        log_success "archiso configs installed at /usr/share/archiso/configs"
    fi
    if [[ -d "${archiso_dir}/archiso" ]]; then
        cp -r "${archiso_dir}/archiso" /usr/share/archiso/
        log_success "archiso libraries installed at /usr/share/archiso/archiso"
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
# Aion archiso profile definition (modern archiso format)
iso_name="${ISO_NAME}"
iso_label="${ISO_LABEL:0:16}"
iso_publisher="Aion Technologies <info@aion.dev>"
iso_application="Aion ${VERSION}"
iso_version="${VERSION}"
install_dir="aion"
buildmodes=('iso')
bootmodes=('uefi.grub')
arch='x86_64'
pacman_conf='pacman.conf'
airootfs_image_type='squashfs'
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--auto-threads' '1')
file_permissions=(
  ["/etc/shadow"]="0:0:0400"
  ["/etc/sudoers.d/aion"]="0:0:0440"
  ["/root"]="0:0:0750"
  ["/home/aion"]="0:0:0750"
)
PROFILEEOF

    log_success "profiledef.sh generated"
}

# Generate pacman.conf tailored for the ISO build with core/extra/community repos.
generate_pacman_conf() {
    log_info "Generating pacman.conf..."

    cat > "${PROFILE_DIR}/pacman.conf" << 'PACMANEOF'
[options]
Architecture = x86_64
CheckSpace
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Optional
ParallelDownloads = 5
Color

[core]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
Server = https://mirror.rackspace.com/archlinux/$repo/os/$arch

[extra]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
Server = https://mirror.rackspace.com/archlinux/$repo/os/$arch

[multilib]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
Server = https://mirror.rackspace.com/archlinux/$repo/os/$arch
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
linux-firmware
mkinitcpio
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

# --- Aion feature runtime dependencies ---
# quick-resume: process checkpoint/restore
criu
# cloud-sync: cloud save backup/restore
rclone
# zero-lag-record: x11grab window geometry fallback
xdotool
# security-bypass-daemon: game sandboxing (inotify fallback is stdlib-only,
# so python-inotify_simple — an AUR-only package — is intentionally NOT used)
bubblewrap

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
kvantum

# --- Input ---
libinput
xf86-input-libinput
xf86-input-evdev

# --- Gaming ---
steam
gamemode
lib32-gamemode
mangohud
lib32-mangohud
wine
wine-staging
lutris
vulkan-intel
lib32-vulkan-intel
vulkan-radeon
lib32-vulkan-radeon
lib32-mesa
inputplumber
ananicy-cpp
goverlay

# --- Emulation ---
retroarch
libretro-core-info

# --- Security Tools ---
polkit
bolt

# --- Network Gaming ---
miniupnpc

# --- Flatpak ---
flatpak

# --- Multimedia Codecs ---
ffmpeg
gstreamer
gst-plugins-base
gst-plugins-good
gst-plugins-bad
gst-plugins-ugly

# --- Boot splash ---
plymouth

# --- Misc ---
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
        etc/aion
        etc/aion/chrome
        etc/aion/performance
        etc/aion/performance/compression
        etc/aion/performance/throttler
        etc/aion/performance/zram
        etc/selinux/aion
        etc/systemd/system
        etc/systemd/system/multi-user.target.wants
        etc/tmpfiles.d
        etc/modules-load.d
        etc/sysctl.d
        etc/xdg/autostart
        usr/lib/aion
        usr/lib/aion/security
        usr/lib/aion/input-engine
        usr/lib/aion/services
        usr/lib/aion/oobe
        usr/lib/aion/wallpaper-engine
        usr/lib/aion/android
        usr/share/aion
        usr/share/aion/plasma
        usr/share/aion/icons
        var/lib/aion
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
    echo "aion" > "${AIROOTFS}/etc/hostname"

    # Hosts
    cat > "${AIROOTFS}/etc/hosts" << 'EOF'
127.0.0.1   localhost
::1         localhost
127.0.1.1   aion.localdomain aion
EOF

    # Locale
    echo "en_US.UTF-8 UTF-8" > "${AIROOTFS}/etc/locale.gen"
    echo "LANG=en_US.UTF-8" > "${AIROOTFS}/etc/locale.conf"
    echo "KEYMAP=us" > "${AIROOTFS}/etc/vconsole.conf"

    # Timezone
    ln -sf /usr/share/zoneinfo/UTC "${AIROOTFS}/etc/localtime"

    # Shadow password — live user. Never a hardcoded default:
    # use AION_LIVE_PASSWORD, else generate a random one (logged once).
    local live_pass
    local encrypted_pass
    if [[ -n "${AION_LIVE_PASSWORD:-}" ]]; then
        live_pass="${AION_LIVE_PASSWORD}"
        if [[ ${#live_pass} -lt 8 ]]; then
            log_error "AION_LIVE_PASSWORD must be at least 8 characters"
            exit 1
        fi
    else
        live_pass="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
        log_warn "No AION_LIVE_PASSWORD set — generated random live password: ${live_pass}"
    fi
    encrypted_pass="$(openssl passwd -6 "${live_pass}")"

    cat > "${AIROOTFS}/etc/passwd" << EOF
root:x:0:0:root:/root:/bin/bash
aion:x:1000:1000:Aion User:/home/aion:/bin/bash
EOF

    cat > "${AIROOTFS}/etc/shadow" << EOF
root:${encrypted_pass}:19999:0:99999:7:::
aion:${encrypted_pass}:19999:0:99999:7:::
EOF

    cat > "${AIROOTFS}/etc/group" << 'EOF'
root:x:0:
wheel:x:10:aion
users:x:1000:aion
video:x:91:aion
audio:x:92:aion
input:x:94:aion
storage:x:95:aion
network:x:96:aion
power:x:97:aion
EOF

    # Sudoers
    mkdir -p "${AIROOTFS}/etc/sudoers.d"
    echo "aion ALL=(ALL:ALL) NOPASSWD: ALL" > "${AIROOTFS}/etc/sudoers.d/aion"
    chmod 440 "${AIROOTFS}/etc/sudoers.d/aion"

    # fstab for Btrfs root with subvolumes
    cat > "${AIROOTFS}/etc/fstab" << 'FSTABEOF'
# Aion Btrfs filesystem layout
# <device>                               <mountpoint>  <type>  <options>                                           <dump> <pass>
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /            btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@         0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /.snapshots  btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@snapshots 0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /home        btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@home    0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /var         btrfs   rw,noatime,compress=zstd:3,ssd,discard=async,subvol=/@var     0  0
UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX  /boot/efi    vfat    rw,noatime,fmask=0077,dmask=0077,codepage=437,iocharset=ascii,shortname=mixed,errors=remount-ro 0  0
tmpfs                                     /tmp         tmpfs   nosuid,nodev,noatime,size=4G                            0  0
FSTABEOF

    # Tmpfiles
    cat > "${AIROOTFS}/etc/tmpfiles.d/aion.conf" << 'EOF'
d /var/log/aion 0755 aion aion -
d /var/lib/aion 0755 aion aion -
d /run/aion 0755 aion aion -
EOF

    # TCP BBR congestion control
    mkdir -p "${AIROOTFS}/etc/modules-load.d"
    cat > "${AIROOTFS}/etc/modules-load.d/aion.conf" << 'BBREOF'
tcp_bbr
BBREOF

    # Gaming network optimizations
    mkdir -p "${AIROOTFS}/etc/sysctl.d"
    cat > "${AIROOTFS}/etc/sysctl.d/99-aion-gaming.conf" << 'SYSCTLEOF'
# TCP BBR Congestion Control
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# Gaming Network Optimizations
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_timestamps = 1
net.ipv4.tcp_sack = 1
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 10
net.ipv4.tcp_no_metrics_save = 1

# Buffer Sizes (16MB max)
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 262144
net.core.wmem_default = 262144
net.ipv4.tcp_rmem = 4096 87380 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216

# Queue / Backlog
net.core.netdev_max_backlog = 5000
net.core.somaxconn = 1024
net.ipv4.tcp_max_syn_backlog = 2048

# Keepalive
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 60
net.ipv4.tcp_keepalive_probes = 3
SYSCTLEOF

    log_success "System configuration written"
}

# =============================================================================
# airootfs: Aion Component Deployment
# =============================================================================

# Copy all Aion core components into the ISO filesystem.
deploy_core_components() {
    log_info "Deploying core components..."

    # Security module
    if [[ -d "${SCRIPT_DIR}/core/security" ]]; then
        cp -a "${SCRIPT_DIR}/core/security/"* "${AIROOTFS}/etc/selinux/aion/"
        mkdir -p "${AIROOTFS}/usr/lib/aion/security"
        cp -a "${SCRIPT_DIR}/core/security/"* "${AIROOTFS}/usr/lib/aion/security/"
        log_success "  core/security → /etc/selinux/aion/ + /usr/lib/aion/security/"
    else
        log_warn "  core/security/ not found, skipping"
    fi

    # Input engine
    if [[ -d "${SCRIPT_DIR}/core/input-engine" ]]; then
        cp -a "${SCRIPT_DIR}/core/input-engine/"* "${AIROOTFS}/usr/lib/aion/input-engine/"
        log_success "  core/input-engine → /usr/lib/aion/input-engine/"
    else
        log_warn "  core/input-engine/ not found, skipping"
    fi

    # Services
    if [[ -d "${SCRIPT_DIR}/core/services" ]]; then
        cp -a "${SCRIPT_DIR}/core/services/"* "${AIROOTFS}/usr/lib/aion/services/"
        log_success "  core/services → /usr/lib/aion/services/"
    else
        log_warn "  core/services/ not found, skipping"
    fi

    # Audio routing (script is invoked as /usr/local/bin/aion-audio-routing.sh)
    if [[ -d "${SCRIPT_DIR}/core/audio" ]]; then
        mkdir -p "${AIROOTFS}/usr/local/bin"
        cp -a "${SCRIPT_DIR}/core/audio/aion-audio-routing.sh" "${AIROOTFS}/usr/local/bin/aion-audio-routing.sh"
        chmod +x "${AIROOTFS}/usr/local/bin/aion-audio-routing.sh"
        cp -a "${SCRIPT_DIR}/core/audio/"*.conf "${AIROOTFS}/etc/pipewire/pipewire.conf.d/" 2>/dev/null || true
        cp -a "${SCRIPT_DIR}/core/audio/"*.conf "${AIROOTFS}/etc/wireplumber/" 2>/dev/null || true
        log_success "  core/audio → /usr/local/bin/ + wireplumber configs"
    else
        log_warn "  core/audio/ not found, skipping"
    fi

    # Network port forwarding (script is invoked as /usr/local/bin/aion-port-forward.sh)
    if [[ -d "${SCRIPT_DIR}/core/network" ]]; then
        mkdir -p "${AIROOTFS}/usr/local/bin"
        cp -a "${SCRIPT_DIR}/core/network/aion-port-forward.sh" "${AIROOTFS}/usr/local/bin/aion-port-forward.sh"
        chmod +x "${AIROOTFS}/usr/local/bin/aion-port-forward.sh"
        log_success "  core/network → /usr/local/bin/"
    else
        log_warn "  core/network/ not found, skipping"
    fi

    # Telemetry collector (invoked as /usr/lib/aion/telemetry_collector.py)
    if [[ -d "${SCRIPT_DIR}/core/telemetry" ]]; then
        cp -a "${SCRIPT_DIR}/core/telemetry/telemetry_collector.py" "${AIROOTFS}/usr/lib/aion/telemetry_collector.py"
        log_success "  core/telemetry → /usr/lib/aion/telemetry_collector.py"
    else
        log_warn "  core/telemetry/ not found, skipping"
    fi

    # Hardware adapter (services reference /opt/aion/core/hardware-adapter/<module>/)
    if [[ -d "${SCRIPT_DIR}/core/hardware-adapter" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/core/hardware-adapter"
        for module_dir in "${SCRIPT_DIR}/core/hardware-adapter/"*/; do
            local module_name
            module_name="$(basename "${module_dir}")"
            mkdir -p "${AIROOTFS}/opt/aion/core/hardware-adapter/${module_name}"
            cp -a "${module_dir}"* "${AIROOTFS}/opt/aion/core/hardware-adapter/${module_name}/"
            # GPU scripts are also exposed on PATH for legacy invocations
            if [[ "${module_name}" == "gpu" ]]; then
                mkdir -p "${AIROOTFS}/usr/local/bin"
                for script in gpu-autodetect.sh egpu-daemon.sh; do
                    if [[ -f "${module_dir}${script}" ]]; then
                        cp -a "${module_dir}${script}" "${AIROOTFS}/usr/local/bin/${script}"
                        chmod +x "${AIROOTFS}/usr/local/bin/${script}"
                    fi
                done
            fi
        done
        log_success "  core/hardware-adapter → /opt/aion/core/hardware-adapter/"
    else
        log_warn "  core/hardware-adapter/ not found, skipping"
    fi

    # Single source of truth for subvolume names/label (consumed by
    # immount-root.sh and any other deployed script).
    mkdir -p "${AIROOTFS}/usr/lib/aion"
    if [[ -f "${SCRIPT_DIR}/build/constants.sh" ]]; then
        cp -a "${SCRIPT_DIR}/build/constants.sh" "${AIROOTFS}/usr/lib/aion/constants.sh"
        log_success "  build/constants.sh → /usr/lib/aion/constants.sh"
    fi
}

# Copy the 4 killer features into the ISO (scripts + systemd units).
# CRITICAL: previously features/ was never deployed, so the ISO shipped
# without quick-resume / vram-scaler / cloud-sync / zero-lag-record.
deploy_features() {
    log_info "Deploying killer features..."

    if [[ ! -d "${SCRIPT_DIR}/features" ]]; then
        log_warn "  features/ not found, skipping"
        return 0
    fi

    mkdir -p "${AIROOTFS}/usr/lib/aion/features"

    # Python feature modules (strip __pycache__)
    find "${SCRIPT_DIR}/features" -maxdepth 1 -name '*.py' -exec cp -a {} "${AIROOTFS}/usr/lib/aion/features/" \;

    # CLI wrappers so features are on PATH
    mkdir -p "${AIROOTFS}/usr/local/bin"
    for f in quick-resume vram-scaler cloud-sync zero-lag-record; do
        if [[ -f "${SCRIPT_DIR}/features/${f}.py" ]]; then
            cat > "${AIROOTFS}/usr/local/bin/aion-${f}" << WRAPEOF
#!/usr/bin/env bash
exec /usr/bin/python3 /usr/lib/aion/features/${f}.py "\$@"
WRAPEOF
            chmod +x "${AIROOTFS}/usr/local/bin/aion-${f}"
        fi
    done

    # systemd units for each feature
    local features_wants="${AIROOTFS}/etc/systemd/system/multi-user.target.wants"
    mkdir -p "${features_wants}"

    # quick-resume daemon
    if [[ -f "${SCRIPT_DIR}/features/quick-resume.py" ]]; then
        cat > "${AIROOTFS}/etc/systemd/system/aion-quick-resume.service" << 'QRSVC'
[Unit]
Description=Aion Quick Resume Daemon (game freeze/restore)
After=local-fs.target
ConditionVirtualization=!container

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/lib/aion/features/quick-resume.py daemon
Restart=on-failure
RestartSec=10
Nice=5

[Install]
WantedBy=multi-user.target
QRSVC
        ln -sf /etc/systemd/system/aion-quick-resume.service "${features_wants}/aion-quick-resume.service"
        log_success "  quick-resume → /usr/lib/aion/features/ + unit"
    fi

    # vram-scaler daemon (AMD only; guarded in the script itself)
    if [[ -f "${SCRIPT_DIR}/features/vram-scaler.py" ]]; then
        cat > "${AIROOTFS}/etc/systemd/system/aion-vram-scaler.service" << 'VRSVC'
[Unit]
Description=Aion Dynamic VRAM Scaler
After=local-fs.target
ConditionPathExists=/sys/class/drm
ConditionVirtualization=!container

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/lib/aion/features/vram-scaler.py daemon
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
VRSVC
        ln -sf /etc/systemd/system/aion-vram-scaler.service "${features_wants}/aion-vram-scaler.service"
        log_success "  vram-scaler → /usr/lib/aion/features/ + unit"
    fi

    # cloud-sync daemon
    if [[ -f "${SCRIPT_DIR}/features/cloud-sync.py" ]]; then
        cat > "${AIROOTFS}/etc/systemd/system/aion-cloud-sync.service" << 'CSSVC'
[Unit]
Description=Aion Cloud Save Sync Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/lib/aion/features/cloud-sync.py sync
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
CSSVC
        ln -sf /etc/systemd/system/aion-cloud-sync.service "${features_wants}/aion-cloud-sync.service"
        log_success "  cloud-sync → /usr/lib/aion/features/ + unit"
    fi

    # zero-lag-record daemon (replay buffer)
    if [[ -f "${SCRIPT_DIR}/features/zero-lag-record.py" ]]; then
        cat > "${AIROOTFS}/etc/systemd/system/aion-zero-lag-record.service" << 'ZRSVC'
[Unit]
Description=Aion Zero-Lag Recording Replay Buffer
After=pipewire.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/lib/aion/features/zero-lag-record.py daemon
Restart=on-failure
RestartSec=10
Nice=5

[Install]
WantedBy=graphical.target
ZRSVC
        ln -sf /etc/systemd/system/aion-zero-lag-record.service "${features_wants}/aion-zero-lag-record.service"
        log_success "  zero-lag-record → /usr/lib/aion/features/ + unit"
    fi
}

# Copy all Aion UI components into the ISO filesystem.
deploy_ui_components() {
    log_info "Deploying UI components..."

    # Plasma config
    if [[ -d "${SCRIPT_DIR}/ui/plasma-config" ]]; then
        cp -a "${SCRIPT_DIR}/ui/plasma-config/"* "${AIROOTFS}/usr/share/aion/plasma/"
        log_success "  ui/plasma-config → /usr/share/aion/plasma/"
    else
        log_warn "  ui/plasma-config/ not found, skipping"
    fi

    # OOBE (out-of-box experience)
    if [[ -d "${SCRIPT_DIR}/ui/oobe" ]]; then
        mkdir -p "${AIROOTFS}/usr/lib/aion/oobe"
        find "${SCRIPT_DIR}/ui/oobe" -maxdepth 1 -type f ! -name '__pycache__' -exec cp -a {} "${AIROOTFS}/usr/lib/aion/oobe/" \;
        log_success "  ui/oobe → /usr/lib/aion/oobe/"
    else
        log_warn "  ui/oobe/ not found, skipping"
    fi

    # Wallpaper engine
    if [[ -d "${SCRIPT_DIR}/ui/wallpaper-engine" ]]; then
        cp -a "${SCRIPT_DIR}/ui/wallpaper-engine/"* "${AIROOTFS}/usr/lib/aion/wallpaper-engine/"
        log_success "  ui/wallpaper-engine → /usr/lib/aion/wallpaper-engine/"
    else
        log_warn "  ui/wallpaper-engine/ not found, skipping"
    fi

    # Icons
    if [[ -d "${SCRIPT_DIR}/ui/icons" ]]; then
        cp -a "${SCRIPT_DIR}/ui/icons/"* "${AIROOTFS}/usr/share/aion/icons/"
        log_success "  ui/icons → /usr/share/aion/icons/"
    else
        log_warn "  ui/icons/ not found, skipping"
    fi

    # Live wallpaper (service references /opt/aion/ui/live-wallpaper/)
    if [[ -d "${SCRIPT_DIR}/ui/live-wallpaper" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/ui/live-wallpaper"
        cp -a "${SCRIPT_DIR}/ui/live-wallpaper/"* "${AIROOTFS}/opt/aion/ui/live-wallpaper/"
        log_success "  ui/live-wallpaper → /opt/aion/ui/live-wallpaper/"
    else
        log_warn "  ui/live-wallpaper/ not found, skipping"
    fi

    # Default live-wallpaper content dir (referenced by config defaults)
    mkdir -p "${AIROOTFS}/usr/share/aion/live-wallpapers"

    # Default live-wallpaper config (read/written by live-wallpaper daemon)
    mkdir -p "${AIROOTFS}/etc/aion"
    cat > "${AIROOTFS}/etc/aion/live-wallpaper.json" << 'LWCONF'
{
  "wallpaper_dir": "/usr/share/aion/live-wallpapers",
  "user_wallpaper_dir": "~/Videos/Aion-Wallpapers",
  "current_wallpaper": "",
  "hw_decoding": "auto",
  "vo_backend": "auto",
  "max_fps": 30,
  "volume": 50,
  "mute": false
}
LWCONF
    log_success "  live-wallpaper config → /etc/aion/live-wallpaper.json"

    # Game capture (service references /opt/aion/ui/game-capture/)
    if [[ -d "${SCRIPT_DIR}/ui/game-capture" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/ui/game-capture"
        cp -a "${SCRIPT_DIR}/ui/game-capture/"* "${AIROOTFS}/opt/aion/ui/game-capture/"
        log_success "  ui/game-capture → /opt/aion/ui/game-capture/"
    else
        log_warn "  ui/game-capture/ not found, skipping"
    fi

    # Default game-capture config (read by game-capture-daemon)
    mkdir -p "${AIROOTFS}/etc/aion"
    cat > "${AIROOTFS}/etc/aion/capture-config.json" << 'CAPCONF'
{
  "enabled": true,
  "buffer_seconds": 30,
  "fps": 60,
  "resolution": "native",
  "encoder": "vaapi",
  "quality": "balanced",
  "save_path": "~/Videos/Aion-Capture",
  "trigger_combo": "Guide+RB",
  "trigger_hold_ms": 2000,
  "pause_wallpaper": true
}
CAPCONF
    log_success "  game-capture config → /etc/aion/capture-config.json"

    # Theme switcher (service references /usr/lib/aion/theme-switcher/)
    if [[ -d "${SCRIPT_DIR}/ui/theme-switcher" ]]; then
        mkdir -p "${AIROOTFS}/usr/lib/aion/theme-switcher"
        cp -a "${SCRIPT_DIR}/ui/theme-switcher/"*.py "${AIROOTFS}/usr/lib/aion/theme-switcher/"
        log_success "  ui/theme-switcher → /usr/lib/aion/theme-switcher/"
    else
        log_warn "  ui/theme-switcher/ not found, skipping"
    fi
}

# Copy Android/Waydroid integration into the ISO filesystem.
deploy_android_components() {
    log_info "Deploying Android components..."

    if [[ -d "${SCRIPT_DIR}/android" ]]; then
        cp -a "${SCRIPT_DIR}/android/"* "${AIROOTFS}/usr/lib/aion/android/"
        log_success "  android/* → /usr/lib/aion/android/"
    else
        log_warn "  android/ not found, skipping"
    fi
}

# Copy performance tuning modules into the ISO filesystem.
deploy_performance_components() {
    log_info "Deploying performance components..."

    if [[ -d "${SCRIPT_DIR}/performance" ]]; then
        # Services reference the runnable scripts under /opt/aion/performance.
        for perf_dir in engine throttler compression; do
            if [[ -d "${SCRIPT_DIR}/performance/${perf_dir}" ]]; then
                mkdir -p "${AIROOTFS}/opt/aion/performance/${perf_dir}"
                cp -a "${SCRIPT_DIR}/performance/${perf_dir}/"*.py "${AIROOTFS}/opt/aion/performance/${perf_dir}/" 2>/dev/null || true
                cp -a "${SCRIPT_DIR}/performance/${perf_dir}/"*.sh "${AIROOTFS}/opt/aion/performance/${perf_dir}/" 2>/dev/null || true
                chmod +x "${AIROOTFS}/opt/aion/performance/${perf_dir}/"* 2>/dev/null || true
            fi
        done
        # Static config (zram generator, etc.) stays under /etc/aion/performance.
        cp -a "${SCRIPT_DIR}/performance/zram/"* "${AIROOTFS}/etc/aion/performance/zram/" 2>/dev/null || true
        log_success "  performance/* → /opt/aion/performance/ + /etc/aion/performance/"
    else
        log_warn "  performance/ not found, skipping"
    fi
}

# Copy top-level config and Chrome configuration into the ISO filesystem.
deploy_config_components() {
    log_info "Deploying config components..."

    if [[ -d "${SCRIPT_DIR}/config" ]]; then
        cp -a "${SCRIPT_DIR}/config/"* "${AIROOTFS}/etc/aion/" 2>/dev/null || true
        log_success "  config/* → /etc/aion/"
    else
        log_warn "  config/ not found, skipping"
    fi

    if [[ -d "${SCRIPT_DIR}/chrome" ]]; then
        cp -a "${SCRIPT_DIR}/chrome/"* "${AIROOTFS}/etc/aion/chrome/" 2>/dev/null || true
        log_success "  chrome/* → /etc/aion/chrome/"
    else
        log_warn "  chrome/ not found, skipping"
    fi
}

# Run all component deployment steps in order.
deploy_all_components() {
    log_step "Deploying Aion components into ISO filesystem"
    deploy_core_components
    deploy_ui_components
    deploy_android_components
    deploy_performance_components
    deploy_features
    deploy_games_components
    deploy_hub_components
    deploy_config_components
    deploy_ota_components
    deploy_boot_splash
    log_success "All components deployed"
}

# Install the Aion Neon Plymouth boot splash theme.
deploy_boot_splash() {
    log_info "Deploying Plymouth boot splash..."

    local theme_src="${SCRIPT_DIR}/boot/plymouth/aion-neon"
    if [[ -d "${theme_src}" ]]; then
        mkdir -p "${AIROOTFS}/usr/share/plymouth/themes/aion-neon"
        cp -a "${theme_src}/"* "${AIROOTFS}/usr/share/plymouth/themes/aion-neon/"
        mkdir -p "${AIROOTFS}/etc/plymouth"
        cat > "${AIROOTFS}/etc/plymouth/plymouthd.conf" << 'PLYCONF'
[Daemon]
Theme=aion-neon
ShowDelay=0
DeviceTimeout=8
PLYCONF
        log_success "  boot/plymouth/aion-neon → /usr/share/plymouth/themes/aion-neon/ + plymouthd.conf"
    else
        log_warn "  boot/plymouth/aion-neon/ not found, skipping"
    fi
}

# Copy game tooling (tweak hub, wine installer) into the ISO filesystem.
# The services reference /opt/aion/games/<module>/ paths.
deploy_games_components() {
    log_info "Deploying games components..."

    if [[ ! -d "${SCRIPT_DIR}/games" ]]; then
        log_warn "  games/ not found, skipping"
        return 0
    fi

    if [[ -d "${SCRIPT_DIR}/games/tweak-hub" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/games/tweak-hub"
        cp -a "${SCRIPT_DIR}/games/tweak-hub/"*.py "${AIROOTFS}/opt/aion/games/tweak-hub/"
        chmod +x "${AIROOTFS}/opt/aion/games/tweak-hub/"*.py 2>/dev/null || true
        log_success "  games/tweak-hub → /opt/aion/games/tweak-hub/"
    fi

    if [[ -d "${SCRIPT_DIR}/games/wine-installer" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/games/wine-installer"
        cp -a "${SCRIPT_DIR}/games/wine-installer/"*.py "${AIROOTFS}/opt/aion/games/wine-installer/" 2>/dev/null || true
        cp -a "${SCRIPT_DIR}/games/wine-installer/wine-optimize.sh" "${AIROOTFS}/opt/aion/games/wine-installer/" 2>/dev/null || true
        log_success "  games/wine-installer  /opt/aion/games/wine-installer/"
    fi

    if [[ -d "${SCRIPT_DIR}/games/emulation" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/games/emulation"
        cp -a "${SCRIPT_DIR}/games/emulation/"*.py "${AIROOTFS}/opt/aion/games/emulation/" 2>/dev/null || true
        chmod +x "${AIROOTFS}/opt/aion/games/emulation/"*.py 2>/dev/null || true
        ln -sf /opt/aion/games/emulation/aion-emu-framework.py "${AIROOTFS}/usr/local/bin/aion-emu"
        log_success "  games/emulation  /opt/aion/games/emulation/ + aion-emu"
    fi
}

# Copy OTA update machinery into the ISO filesystem.
# ota-updater.py runs as /usr/lib/aion/ota-updater.py; the release public key
# lives in /etc/aion/gpg/ so signatures can be verified without network writes.
deploy_ota_components() {
    log_info "Deploying OTA components..."

    if [[ ! -d "${SCRIPT_DIR}/deploy/ota" ]]; then
        log_warn "  deploy/ota/ not found, skipping"
        return 0
    fi

    cp -a "${SCRIPT_DIR}/deploy/ota/ota-updater.py" "${AIROOTFS}/usr/lib/aion/ota-updater.py" 2>/dev/null || true
    cp -a "${SCRIPT_DIR}/deploy/ota/ota_compression.py" "${AIROOTFS}/usr/lib/aion/ota_compression.py" 2>/dev/null || true
    chmod +x "${AIROOTFS}/usr/lib/aion/ota-updater.py" 2>/dev/null || true

    mkdir -p "${AIROOTFS}/etc/aion/gpg"
    if [[ -f "${SCRIPT_DIR}/deploy/ota/aion-release.asc" ]]; then
        cp -a "${SCRIPT_DIR}/deploy/ota/aion-release.asc" "${AIROOTFS}/etc/aion/gpg/aion-release.asc"
    fi
    log_success "  deploy/ota → /usr/lib/aion/ota-updater.py + /etc/aion/gpg/"
}

# Copy the Aion Hub portal (server, manifest, web assets, password helper)
# into the ISO filesystem under /opt/aion/hub.
deploy_hub_components() {
    log_info "Deploying Aion Hub..."
    if [[ -d "${SCRIPT_DIR}/hub" ]]; then
        mkdir -p "${AIROOTFS}/opt/aion/hub"
        cp -a "${SCRIPT_DIR}/hub/." "${AIROOTFS}/opt/aion/hub/"
        chmod +x "${AIROOTFS}/opt/aion/hub/aion-hub-server.py" 2>/dev/null || true
        chmod +x "${AIROOTFS}/opt/aion/hub/aion-hub-pass.py" 2>/dev/null || true
        log_success "  hub/ → /opt/aion/hub/"
    else
        log_warn "  hub/ not found, skipping"
    fi
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
        "aion-init.service"
        "aion-security.service"
        "aion-input.service"
        "aion-throttler.service"
        "aion-chameleon-memory.service"
        "aion-gpu-autodetect.service"
        "aion-gpu-profiler.service"
        "aion-egpu.service"
        "aion-storage-optimizer.service"
        "aion-performance-engine.service"
        "aion-telemetry.service"
        "aion-hub.service"
        "aion-key-mapper.service"
        "aion-live-wallpaper.service"
        "aion-game-capture.service"
        "nexus-theme-switcher.service"
        "nexus-tweak-hub.service"
        "aion-gpu-monitor.service"
        "aion-ota.service"
        "aion-ota-silent.service"
    )

    for svc in "${service_files[@]}"; do
        local found=0
        # Search all source directories for this service file
        for search_dir in "${SCRIPT_DIR}/core" "${SCRIPT_DIR}/ui" "${SCRIPT_DIR}/performance" "${SCRIPT_DIR}/hub" "${SCRIPT_DIR}/games" "${SCRIPT_DIR}/deploy/ota"; do
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

    # Create a placeholder for the Aion boot service
    cat > "${AIROOTFS}/etc/systemd/system/aion-boot-setup.service" << 'BOOTEOF'
[Unit]
Description=Aion Boot Setup - Configures immutable Btrfs root
After=local-fs.target
Before=graphical.target
ConditionPathExists=!/var/lib/aion/.boot-configured

[Service]
Type=oneshot
ExecStart=/usr/lib/aion/services/immount-root.sh --setup
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
BOOTEOF
    ln -sf /etc/systemd/system/aion-boot-setup.service \
        "${services_wants}/aion-boot-setup.service"

    # Create the Aion user-session target that UI/game services bind to.
    # It is pulled in by the graphical session and starts the live-wallpaper,
    # game-capture, and tweak-hub daemons once the desktop is up.
    cat > "${AIROOTFS}/etc/systemd/system/aion-session.target" << 'SESSIONTGT'
[Unit]
Description=Aion User Session Services
After=graphical.target
Wants=graphical.target
PartOf=graphical.target

[Install]
WantedBy=graphical.target
SESSIONTGT
    ln -sf /etc/systemd/system/aion-session.target \
        "${services_wants}/aion-session.target"

    # Enable audio routing service
    cat > "${AIROOTFS}/etc/systemd/system/aion-audio-routing.service" << 'AUDIOSVC'
[Unit]
Description=Aion Per-App Audio Router
After=pipewire.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/aion-audio-routing.sh --setup
RemainAfterExit=yes

[Install]
WantedBy=graphical.target
AUDIOSVC
    ln -sf /etc/systemd/system/aion-audio-routing.service "${services_wants}/aion-audio-routing.service"

    # Enable network port forwarding timer
    cat > "${AIROOTFS}/etc/systemd/system/aion-port-forward.service" << 'NETSVC'
[Unit]
Description=Aion Gaming Port Forwarding
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/aion-port-forward.sh start
ExecStop=/usr/local/bin/aion-port-forward.sh stop

[Install]
WantedBy=multi-user.target
NETSVC
    cat > "${AIROOTFS}/etc/systemd/system/aion-port-forward.timer" << 'NETTMR'
[Unit]
Description=Refresh gaming port forwards every 30 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
NETTMR
    ln -sf /etc/systemd/system/aion-port-forward.timer "${services_wants}/aion-port-forward.timer"

    # Enable Btrfs scrub weekly timer
    cat > "${AIROOTFS}/etc/systemd/system/aion-btrfs-scrub.service" << 'BTRSSVC'
[Unit]
Description=Aion Btrfs Filesystem Scrub
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/btrfs scrub start /
Nice=19
IOSchedulingClass=idle
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
BTRSSVC
    cat > "${AIROOTFS}/etc/systemd/system/aion-btrfs-scrub.timer" << 'BTRSTMR'
[Unit]
Description=Weekly Btrfs filesystem scrub

[Timer]
OnCalendar=weekly
RandomizedDelaySec=1d
Persistent=true

[Install]
WantedBy=timers.target
BTRSTMR
    ln -sf /etc/systemd/system/aion-btrfs-scrub.timer "${services_wants}/aion-btrfs-scrub.timer"

    # Enable ananicy-cpp
    cat > "${AIROOTFS}/etc/systemd/system/aion-ananicy.service" << 'ANANSVC'
[Unit]
Description=Aion Auto Nice Daemon
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/ananicy-cpp
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
ANANSVC
    ln -sf /etc/systemd/system/aion-ananicy.service "${services_wants}/aion-ananicy.service"

    log_success "Systemd services configured"
}

# =============================================================================
# airootfs: Btrfs Immutable Root Setup
# =============================================================================

# Install the immutable Btrfs root initialization script that runs on first boot.
setup_immutable_root() {
    log_step "Setting up immutable Btrfs root"

    cat > "${AIROOTFS}/usr/lib/aion/services/immount-root.sh" << 'IMROOT'
#!/usr/bin/env bash
# Aion Immutable Btrfs Root Setup
# Runs once on first boot to configure Btrfs subvolume layout.
# This script is idempotent — safe to re-run.

set -euo pipefail

LOG_FILE="/var/log/aion/boot-setup.log"
mkdir -p "$(dirname "${LOG_FILE}")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log "Aion immutable root setup starting..."

# Detect root device
ROOT_DEV="$(findmnt -n -o SOURCE /)"
if [[ -z "${ROOT_DEV}" ]]; then
    log "ERROR: Cannot detect root device"
    exit 1
fi

# Check if already configured
if [[ -f /var/lib/aion/.boot-configured ]]; then
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
if [[ -f /etc/aion/performance/zram/zram-generator.conf ]]; then
    cp /etc/aion/performance/zram/zram-generator.conf /etc/zram-generator.conf 2>/dev/null || true
    log "zram configuration applied"
fi

# Configure Btrfs compression hints
if [[ -f /etc/aion/performance/compression/btrfs-compression.sh ]]; then
    chmod +x /etc/aion/performance/compression/btrfs-compression.sh
    log "Btrfs compression script installed"
fi

# Mark as configured
touch /var/lib/aion/.boot-configured
log "Aion immutable root setup complete"

exit 0
IMROOT
    chmod +x "${AIROOTFS}/usr/lib/aion/services/immount-root.sh"

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
    mkdir -p "${AIROOTFS}/home/aion"
    mkdir -p "${AIROOTFS}/home/aion/Desktop"
    mkdir -p "${AIROOTFS}/home/aion/Documents"
    mkdir -p "${AIROOTFS}/home/aion/Downloads"
    mkdir -p "${AIROOTFS}/home/aion/.config"
    mkdir -p "${AIROOTFS}/home/aion/.local/share"
    mkdir -p "${AIROOTFS}/home/aion/.local/share/applications"

    # Set ownership (uid 1000 = aion user)
    chown -hR 1000:1000 "${AIROOTFS}/home/aion" 2>/dev/null || true

    # Shell profile
    cat > "${AIROOTFS}/home/aion/.bash_profile" << 'BASHPROF'
# Aion user profile
export XDG_CURRENT_DESKTOP=KDE
export XDG_SESSION_DESKTOP=KDE
export XDG_SESSION_TYPE=wayland
export QT_QPA_PLATFORM=wayland
export MOZ_ENABLE_WAYLAND=1
export NEXUS_HOME=/usr/lib/aion
export NEXUS_CONFIG=/etc/aion

# Launch SDDM on tty1 login
if [[ -z "${DISPLAY}" && "${XDG_VTNR}" -eq 1 ]]; then
    exec startplasma-wayland
fi
BASHPROF
    chown 1000:1000 "${AIROOTFS}/home/aion/.bash_profile" 2>/dev/null || true

    # KDE autostart for OOBE
    cat > "${AIROOTFS}/etc/xdg/autostart/aion-oobe.desktop" << 'OOBEOF'
[Desktop Entry]
Type=Application
Name=Aion Setup Wizard
Comment=Aion first-boot configuration wizard
Exec=/usr/lib/aion/oobe/oobe_wizard.py
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after-panel=true
OOBEOF

    # SDDM configuration
    mkdir -p "${AIROOTFS}/etc/sddm.conf.d"
    cat > "${AIROOTFS}/etc/sddm.conf.d/aion.conf" << 'SDDMEOF'
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
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR="Aion"
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash nvidia-drm.modeset=1 mitigations=off"
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

    cat > "${AIROOTFS}/boot/grub/grub.cfg" << GRUBCFG
set default=0
set timeout=3

serial --speed=115200 --unit=0 --word=8 --parity=no --stop=1
terminal_input serial console
terminal_output serial console

set menu_color_normal=white/black
set menu_color_highlight=black/light-gray

menuentry "Aion ${VERSION}" --class aion --class gnu-linux --class os {
    search --no-floppy --fs-uuid --set=root XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
    linux /boot/vmlinuz-linux-zen root=UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX rootflags=subvol=/@ rw quiet splash console=ttyS0,115200n8
    initrd /boot/initramfs-linux-zen.img
}

menuentry "Aion ${VERSION} (Recovery)" --class aion --class gnu-linux --class os {
    search --no-floppy --fs-uuid --set=root XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
    linux /boot/vmlinuz-linux-zen root=UUID=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX rootflags=subvol=/@ rw single console=ttyS0,115200n8
    initrd /boot/initramfs-linux-zen.img
}

menuentry "UEFI Firmware Settings" --class firmware {
    fwsetup
}
GRUBCFG

    log_success "GRUB configured"
}

# Write the grub.cfg used by mkarchiso's 'uefi.grub' boot mode (lives in the profile dir,
# placeholders %INSTALL_DIR%/%ARCH%/%ARCHISO_UUID% are substituted by mkarchiso).
generate_profile_grub() {
    log_info "Generating profile GRUB config..."

    mkdir -p "${PROFILE_DIR}/grub"

    cat > "${PROFILE_DIR}/grub/grub.cfg" << 'PROGRUBCFG'
# Aion archiso GRUB config (uefi.grub boot mode)
if loadfont "${prefix}/fonts/unicode.pf2" ; then
    insmod all_video
    set gfxmode="auto"
    terminal_input console
    terminal_output console
fi

insmod serial
if serial --unit=0 --speed=115200; then
    terminal_input --append serial
    terminal_output --append serial
fi

if [ "${grub_platform}" == 'efi' ]; then
    archiso_platform='UEFI'
elif [ "${grub_platform}" == 'pc' ]; then
    archiso_platform='BIOS'
else
    archiso_platform="${grub_cpu}-${grub_platform}"
fi

set default="aion"
set timeout=3
set timeout_style=menu

menuentry "Aion (%ARCH%, ${archiso_platform})" --class aion --class gnu-linux --class gnu --class os --id 'aion' {
    set gfxpayload=keep
    linux /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux-zen archisobasedir=%INSTALL_DIR% archisosearchuuid=%ARCHISO_UUID%
    initrd /%INSTALL_DIR%/boot/%ARCH%/initramfs-linux-zen.img
}

menuentry "Aion fallback (%ARCH%, ${archiso_platform})" --class aion --class gnu-linux --class gnu --class os --id 'aion-fallback' {
    set gfxpayload=keep
    linux /%INSTALL_DIR%/boot/%ARCH%/vmlinuz-linux-zen archisobasedir=%INSTALL_DIR% archisosearchuuid=%ARCHISO_UUID%
    initrd /%INSTALL_DIR%/boot/%ARCH%/initramfs-linux-zen-fallback.img
}

if [ "${grub_platform}" == 'efi' ]; then
    menuentry 'UEFI Firmware Settings' --class firmware {
        fwsetup
    }
fi

menuentry 'System shutdown' --class shutdown --class poweroff {
    halt
}

menuentry 'System restart' --class reboot --class restart {
    reboot
}
PROGRUBCFG

    log_success "Profile GRUB config generated"
}

# =============================================================================
# airootfs: Syslinux/Isolinux Configuration (BIOS Boot)
# =============================================================================

# Install Syslinux/Isolinux bootloader configuration for BIOS/legacy boot.
# This ensures BIOS machines also get unattended auto-boot with 3-second timeout.
configure_syslinux() {
    log_step "Configuring Syslinux/Isolinux bootloader"

    local syslinux_dir="${AIROOTFS}/boot/syslinux"
    mkdir -p "${syslinux_dir}"

    cat > "${syslinux_dir}/syslinux.cfg" << 'SYSLINUXCFG'
PROMPT 0
TIMEOUT 30
DEFAULT aion

UI /boot/syslinux/vesamenu.c32

MENU TITLE Aion Boot Menu
MENU BACKGROUND splash.png
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

LABEL aion-recovery
    MENU LABEL Aion (recovery)
    LINUX /boot/vmlinuz-linux
    INITRD /boot/initramfs-linux.img
    APPEND root=LABEL=AION_ROOT rw rootflags=subvol=@ single

LABEL reboot
    MENU LABEL Reboot
    COM32 chain.c32
    APPEND reboot

LABEL poweroff
    MENU LABEL Power Off
    COM32 chain.c32
    APPEND poweroff
SYSLINUXCFG

    log_success "Syslinux configured"
}

# =============================================================================
# Boot Timeout Safety Net — Patch All Timeout Values
# =============================================================================

# Final safety net: scan all boot config files and enforce timeout=3.
# Catches any timeout value that slipped through manual edits.
# Runs after all individual boot config functions complete.
patch_boot_timeout() {
    log_step "Patching boot timeout values (safety net)"

    local patched=0

    # --- GRUB: /etc/default/grub (GRUB_TIMEOUT=N) ---
    local grub_default="${AIROOTFS}/etc/default/grub"
    if [[ -f "${grub_default}" ]]; then
        if grep -q "^GRUB_TIMEOUT=" "${grub_default}" 2>/dev/null; then
            sed -i 's/^GRUB_TIMEOUT=[0-9]*/GRUB_TIMEOUT=3/' "${grub_default}"
            log_success "Patched GRUB_TIMEOUT in ${grub_default}"
            patched=$((patched + 1))
        fi
    fi

    # --- GRUB: boot/grub/grub.cfg (set timeout=N) ---
    local grub_cfg="${AIROOTFS}/boot/grub/grub.cfg"
    if [[ -f "${grub_cfg}" ]]; then
        if grep -q "^set timeout=" "${grub_cfg}" 2>/dev/null; then
            sed -i 's/^set timeout=[0-9]*/set timeout=3/' "${grub_cfg}"
            log_success "Patched set timeout in ${grub_cfg}"
            patched=$((patched + 1))
        fi
    fi

    # --- Syslinux: boot/syslinux/syslinux.cfg (TIMEOUT N) ---
    local syslinux_cfg="${AIROOTFS}/boot/syslinux/syslinux.cfg"
    if [[ -f "${syslinux_cfg}" ]]; then
        if grep -q "^TIMEOUT " "${syslinux_cfg}" 2>/dev/null; then
            sed -i 's/^TIMEOUT [0-9]*/TIMEOUT 30/' "${syslinux_cfg}"
            log_success "Patched TIMEOUT in ${syslinux_cfg}"
            patched=$((patched + 1))
        fi
    fi

    # --- systemd-boot: loader.conf (timeout N) ---
    local loader_conf="${AIROOTFS}/boot/loader/loader.conf"
    if [[ -f "${loader_conf}" ]]; then
        if grep -q "^timeout " "${loader_conf}" 2>/dev/null; then
            sed -i 's/^timeout [0-9]*/timeout 3/' "${loader_conf}"
            log_success "Patched timeout in ${loader_conf}"
            patched=$((patched + 1))
        fi
    fi

    if [[ "${patched}" -eq 0 ]]; then
        log_warn "No boot config files found to patch (expected at least grub.cfg)"
    else
        log_success "Boot timeout patched in ${patched} config file(s) — all set to 3s"
    fi
}

# =============================================================================
# airootfs: mkinitcpio Configuration
# =============================================================================

# Write mkinitcpio.conf with Btrfs and essential modules for boot.
configure_mkinitcpio() {
    log_info "Configuring mkinitcpio..."

    cat > "${AIROOTFS}/etc/mkinitcpio.conf" << 'MKINIEOF'
MODULES=(btrfs amdgpu radeon i915 nvidia nvidia_modeset nvidia_uvm nvidia_drm)
BINARIES=()
FILES=()
HOOKS=(base udev plymouth autodetect modconf kms keyboard keymap consolefont block filesystems fsck)
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
    : > "${checksum_file}"
    if [[ -f "${ISO_NAME}.iso" ]]; then
        sha256sum "${ISO_NAME}.iso" > "${checksum_file}"
    fi
    if [[ -f "${ISO_NAME}.iso.xz" ]]; then
        sha256sum "${ISO_NAME}.iso.xz" >> "${checksum_file}"
    fi
    cd "${SCRIPT_DIR}"

    if [[ ! -s "${checksum_file}" ]]; then
        log_error "No artifacts found to checksum in ${OUT_DIR}"
        exit 1
    fi

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

    xz -9 -T 0 -k "${iso_path}"
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
    echo -e "${BOLD}${WHITE}  BUILD COMPLETE — Aion ${VERSION}${NC}"
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

    # Verify we are in the Aion project root
    if [[ ! -f "${SCRIPT_DIR}/core/security/aion-security.service" ]]; then
        log_error "Cannot find Aion project files. Run this script from the Aion root directory."
        log_error "Expected: ${SCRIPT_DIR}/core/security/aion-security.service"
        exit 1
    fi
    log_success "Aion project root verified"

    # Verify required tools (skip mkarchiso — installed by install_dependencies below)
    local required_tools=("xz" "openssl")
    for tool in "${required_tools[@]}"; do
        if ! command -v "${tool}" &>/dev/null; then
            log_error "Missing required tool: ${tool}"
            exit 1
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

# Orchestrates the entire Aion build process from dependency installation
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
    generate_profile_grub
    configure_syslinux
    patch_boot_timeout
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
