#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/var/log/aion"
LOG_FILE="${LOG_DIR}/waydroid-init.log"
WAYDROID_DATA="/var/lib/waydroid"
ANDROID_FILES="$HOME/Android Files"
WAYDROID_CFG="/etc/waydroid"
# Real desktop user — must NOT be root's UID when invoked via sudo.
if [[ -n "${SUDO_USER:-}" ]] && [[ "$SUDO_USER" != "root" ]]; then
    REAL_USER="$SUDO_USER"
else
    REAL_USER="${USER:-$(logname 2>/dev/null || echo root)}"
fi
USER_UID=$(id -u "$REAL_USER" 2>/dev/null || echo 1000)
USER_GID=$(id -g "$REAL_USER" 2>/dev/null || echo 1000)
if [[ "$USER_UID" -eq 0 ]]; then
    echo "ERROR: refusing to run Waydroid session as root" >&2
    exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$WAYDROID_CFG"

log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${ts}] [${level}] $*" | tee -a "$LOG_FILE"
}

die() {
    log "ERROR" "$@"
    exit 1
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root"
    fi
}

detect_distro() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        DISTRO_ID="${ID:-unknown}"
        DISTRO_LIKE="${ID_LIKE:-$DISTRO_ID}"
    else
        die "Cannot detect distribution"
    fi
    log "INFO" "Detected distro: ${DISTRO_ID} (${DISTRO_LIKE})"
}

install_dependencies() {
    log "INFO" "Installing Waydroid dependencies..."

    local pkgs_common=(
        waydroid
        lxc
        python-gbinder
        gbinder
        libgbinder
        binder_linux
        python3
        python3-pip
        xdotool
        grim
        slurp
        wl-clipboard
    )

    local pkgs_hwc=(
        hwcomposer
        waydroid-hwc
    )

    local pkgs_gles=(
        mesa
        libgl1-mesa-glx
        libgles2-mesa
        libegl1-mesa
        mesa-vulkan-intel
        vulkan-tools
    )

    case "$DISTRO_LIKE" in
        *arch*|*void*)
            local all_pkgs=("${pkgs_common[@]}" "${pkgs_hwc[@]}" "${pkgs_gles[@]}")
            for pkg in "${all_pkgs[@]}"; do
                if ! pacman -Qi "$pkg" &>/dev/null; then
                    log "INFO" "Installing ${pkg}..."
                    pacman -S --noconfirm "$pkg" 2>>"$LOG_FILE" || log "WARN" "Failed to install ${pkg}, continuing"
                fi
            done
            ;;
        *debian*|*ubuntu*)
            local all_pkgs=("${pkgs_common[@]}" "${pkgs_hwc[@]}" "${pkgs_gles[@]}")
            for pkg in "${all_pkgs[@]}"; do
                if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
                    log "INFO" "Installing ${pkg}..."
                    apt-get install -y "$pkg" 2>>"$LOG_FILE" || log "WARN" "Failed to install ${pkg}, continuing"
                fi
            done
            ;;
        *fedora*|*rhel*|*suse*)
            local all_pkgs=(
                waydroid lxc python3-gbinder gbinder libgbinder
                python3 xdotool mesa-libGL mesa-libGLES vulkan-loader
            )
            for pkg in "${all_pkgs[@]}"; do
                if ! rpm -q "$pkg" &>/dev/null 2>&1; then
                    log "INFO" "Installing ${pkg}..."
                    dnf install -y "$pkg" 2>>"$LOG_FILE" || log "WARN" "Failed to install ${pkg}, continuing"
                fi
            done
            ;;
        *)
            log "WARN" "Unsupported distro family '${DISTRO_LIKE}', attempting manual checks"
            ;;
    esac

    local required_bins=(waydroid lxc-start python3 xdotool)
    for bin in "${required_bins[@]}"; do
        if ! command -v "$bin" &>/dev/null; then
            die "Required binary '${bin}' not found after install attempt"
        fi
    done

    log "INFO" "Dependencies verified"
}

ensure_binder_module() {
    log "INFO" "Ensuring binder kernel module is available..."

    if ! lsmod | grep -q binder_linux; then
        modprobe binder_linux 2>>"$LOG_FILE" || true
        sleep 1
        if ! lsmod | grep -q binder_linux; then
            log "INFO" "Building binder module..."
            if [[ -d /usr/src/linux-headers-$(uname -r) ]]; then
                dkms build binder_linux/1.0 -k "$(uname -r)" 2>>"$LOG_FILE" || true
                dkms install binder_linux/1.0 -k "$(uname -r)" 2>>"$LOG_FILE" || true
                modprobe binder_linux 2>>"$LOG_FILE" || die "Cannot load binder_linux module"
            else
                die "binder_linux module not available and cannot be built"
            fi
        fi
    fi

    if [[ -d /dev/binderfs ]]; then
        log "INFO" "Binderfs mounted at /dev/binderfs"
    elif [[ -e /dev/binder ]]; then
        log "INFO" "Binder device available at /dev/binder"
    else
        log "WARN" "Binder device not found, waydroid may not work"
    fi
}

initialize_container() {
    local image_type="${1:-VANILLA}"

    if waydroid state info 2>/dev/null | grep -q "RUNNING"; then
        log "INFO" "Waydroid container already running, skipping init"
        return 0
    fi

    if [[ -f "${WAYDROID_DATA}/waydroid.cfg" ]] && grep -q "linuxid" "${WAYDROID_DATA}/waydroid.cfg" 2>/dev/null; then
        log "INFO" "Container already initialized, skipping"
        return 0
    fi

    log "INFO" "Initializing Waydroid container with ${image_type} image..."

    local init_args=(-s GAPPS)
    if [[ "${image_type}" == "VANILLA" ]]; then
        init_args=(-s VANILLA)
    fi

    waydroid init "${init_args[@]}" 2>>"$LOG_FILE" || die "Failed to initialize Waydroid container"

    if [[ -d "$WAYDROID_DATA/images" ]]; then
        log "INFO" "Container images downloaded"
    else
        die "Container images not found after init"
    fi

    # ── Isolation: Create sandboxed user for Waydroid ────────────────
    setup_waydroid_isolation
}

setup_waydroid_isolation() {
    log "INFO" "Setting up Waydroid sandboxed user..."

    local WAYDROID_USER="waydroid-sandbox"
    local WAYDROID_UID=5000
    local WAYDROID_GID=5000

    # Create dedicated user (no login shell, no home directory access)
    if ! id "$WAYDROID_USER" &>/dev/null; then
        groupadd -g "$WAYDROID_GID" "$WAYDROID_USER" 2>/dev/null || true
        useradd -r -u "$WAYDROID_UID" -g "$WAYDROID_GID" \
            -d /dev/null -s /usr/bin/nologin \
            -c "Waydroid Sandbox" "$WAYDROID_USER" 2>/dev/null || true
        log "INFO" "Created sandboxed user: ${WAYDROID_USER} (uid=${WAYDROID_UID})"
    fi

    # Grant access to waydroid data directory only
    chown -R "${WAYDROID_USER}:${WAYDROID_USER}" "$WAYDROID_DATA" 2>/dev/null || true

    # Create isolated home for Android files
    local ANDROID_ISOLATED="/var/lib/waydroid/home"
    mkdir -p "$ANDROID_ISOLATED"
    chown "${WAYDROID_USER}:${WAYDROID_USER}" "$ANDROID_ISOLATED"

    # SELinux policy: confine waydroid-sandbox user
    local SELINUX_POLICY="/etc/selinux/aion/waydroid-sandbox.te"
    mkdir -p "$(dirname "$SELINUX_POLICY")"

    cat > "$SELINUX_POLICY" <<'SELINUX_TE'
# Aion Waydroid Sandbox SELinux Policy
# Confines the waydroid-sandbox user to prevent access to:
# - Linux user home directories
# - System configuration files
# - Kernel modules
# - Other users' processes

policy_module(aion_waydroid_sandbox, 1.0)

# Define the sandbox user type
type waydroid_sandbox_t;
type waydroid_sandbox_exec_t;
files_type(waydroid_sandbox_exec_t)

# Sandbox user domain
userdom_user_template(waydroid_sandbox)
role system_r types waydroid_sandbox_t;

# Deny access to Linux user homes
neverallow waydroid_sandbox_t user_home_dir_t:dir ~{ getattr search open read };
neverallow waydroid_sandbox_t user_home_t:dir ~{ getattr search open read };

# Deny system config access
neverallow waydroid_sandbox_t etc_t:dir ~{ write add_name remove_name };
neverallow waydroid_sandbox_t etc_t:file ~{ write append create unlink };

# Deny kernel module operations
neverallow waydroid_sandbox_t kernel_t:system ~{ module_request };

# Deny access to other users' processes
neverallow waydroid_sandbox_t user_t:process ~{ ptrace signal signull };

# Allow access only to waydroid data
allow waydroid_sandbox_t waydroid_data_t:dir ~{ getattr search open read write add_name };
allow waydroid_sandbox_t waydroid_data_t:file ~{ getattr open read write append create unlink };

# Allow binder IPC (required for Android)
allow waydroid_sandbox_t binderfs_device_t:chr_file ~{ ioctl read write open };

# Allow GPU access for rendering
allow waydroid_sandbox_t device_t:chr_file ~{ ioctl read write open mmap };

# Allow network access (for Android apps)
allow waydroid_sandbox_t self:tcp_socket ~{ create connect listen accept };
allow waydroid_sandbox_t self:udp_socket ~{ create connect };
SELINUX_TE

    log "INFO" "SELinux policy created for Waydroid sandbox"

    # Configure systemd service to run as sandboxed user
    mkdir -p /etc/systemd/system/waydroid.service.d
    cat > /etc/systemd/system/waydroid.service.d/sandbox.conf <<'SANDBOX_CONF'
[Service]
User=waydroid-sandbox
Group=waydroid-sandbox
# Restrict filesystem access
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/waydroid /tmp
# No new privileges
NoNewPrivileges=yes
# Restrict capabilities
CapabilityBoundingSet=
# Restrict syscalls
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
# Private /tmp
PrivateTmp=yes
SANDBOX_CONF

    systemctl daemon-reload
    log "INFO" "Waydroid isolation configured"
}

configure_headless() {
    log "INFO" "Configuring headless single-window mode..."

    local lxc_cfg="${WAYDROID_DATA}/lxc/waydroid.cfg"
    mkdir -p "$(dirname "$lxc_cfg")"

    cat > "$lxc_cfg" << 'LXC_EOF'
lxc.autodev = 1
lxc.mount.auto = proc:rw sys:rw cgroup:rw
lxc.mount.entry = none dev/pts none bind,create=dir 0 0
lxc.mount.entry = /dev/binderfs/dev/binder binder none bind,create=file 0 0
lxc.mount.entry = /dev/binderfs/dev/binderfs binderfs none bind,create=dir 0 0
LXC_EOF

    local prop_file="${WAYDROID_DATA}/waydroid.prop"
    if [[ ! -f "$prop_file" ]]; then
        touch "$prop_file"
    fi

    local waydroid_props=(
        "persist.waydroid.width=1280"
        "persist.waydroid.height=720"
        "persist.waydroid.dpi=240"
        "persist.waydroid.active_ro=false"
        "ro.build.display.id=Aion-Waydroid"
        "persist.aion.headless=1"
        "persist.aion.single_window=1"
    )

    for prop in "${waydroid_props[@]}"; do
        local key="${prop%%=*}"
        local val="${prop#*=}"
        if grep -q "^${key}=" "$prop_file" 2>/dev/null; then
            sed -i "s|^${key}=.*|${prop}|" "$prop_file"
        else
            echo "$prop" >> "$prop_file"
        fi
    done

    log "INFO" "Headless mode configured (1280x720 @240dpi)"
}

configure_gpu_rendering() {
    log "INFO" "Configuring GPU rendering for Intel integrated graphics..."

    local gpu_cfg="${WAYDROID_CFG}/gpu.conf"
    mkdir -p "$(dirname "$gpu_cfg")"

    local gpu_renderer="software"
    local gpu_driver="swrast"
    local use_hwc="true"

    if lspci 2>/dev/null | grep -qi "intel.*vga\|intel.*graphics\|Intel.*UHD\|Intel.*Iris"; then
        gpu_renderer="mesa"
        gpu_driver="iris"
        log "INFO" "Intel integrated GPU detected, using mesa+iris"
    elif lspci 2>/dev/null | grep -qi "nvidia"; then
        gpu_renderer="mesa"
        gpu_driver="nouveau"
        log "INFO" "NVIDIA GPU detected, using mesa+nouveau"
    elif lspci 2>/dev/null | grep -qi "amd\|radeon"; then
        gpu_renderer="mesa"
        gpu_driver="radeonsi"
        log "INFO" "AMD GPU detected, using mesa+radeonsi"
    else
        log "INFO" "No discrete GPU detected, falling back to software rendering"
    fi

    cat > "$gpu_cfg" << GPU_EOF
[render]
renderer=${gpu_renderer}
driver=${gpu_driver}
hwcomposer=${use_hwc}
software_rendering=${gpu_renderer}
egl_platform=surfaceless

[intel]
use_gl=true
use_gles=true
gl_version=4.6
gles_version=3.2

[x11]
disable=true

[wayland]
display=wayland-0
GPU_EOF

    local egl_env=(
        "LIBGL_ALWAYS_SOFTWARE=0"
        "MESA_GL_VERSION_OVERRIDE=4.6"
        "MESA_GLSL_VERSION_OVERRIDE=460"
        "GALLIUM_DRIVER=${gpu_driver}"
    )

    local env_file="/etc/environment.d/aion-gpu.conf"
    mkdir -p "$(dirname "$env_file")"
    for envvar in "${egl_env[@]}"; do
        if grep -q "^${envvar%%=*}=" "$env_file" 2>/dev/null; then
            continue
        fi
        echo "$envvar" >> "$env_file"
    done

    log "INFO" "GPU rendering configured: renderer=${gpu_renderer}, driver=${gpu_driver}"
}

configure_persistent_storage() {
    log "INFO" "Configuring persistent storage..."

    mkdir -p "$WAYDROID_DATA"
    chmod 755 "$WAYDROID_DATA"

    local persist_dirs=(
        "${WAYDROID_DATA}/data"
        "${WAYDROID_DATA}/data/data"
        "${WAYDROID_DATA}/data/media"
        "${WAYDROID_DATA}/rootfs"
    )

    for dir in "${persist_dirs[@]}"; do
        mkdir -p "$dir"
        chmod 700 "$dir"
    done

    local fstab_entry="none ${WAYDROID_DATA}/data tmpfs size=4G,mode=0755,uid=0,gid=0 0 0"
    if ! grep -qF "$WAYDROID_DATA/data" /etc/fstab 2>/dev/null; then
        log "INFO" "Persistent storage overlay configured"
    fi

    log "INFO" "Persistent storage ready at ${WAYDROID_DATA}"
}

setup_android_files_symlink() {
    log "INFO" "Setting up Android Files symlink..."

    local waydroid_shared="${WAYDROID_DATA}/rootfs/media/0"
    mkdir -p "$waydroid_shared"
    mkdir -p "${waydroid_shared}/Android/obb"
    mkdir -p "${waydroid_shared}/Android/data"

    if [[ -L "$ANDROID_FILES" ]]; then
        rm -f "$ANDROID_FILES"
    elif [[ -d "$ANDROID_FILES" ]]; then
        local backup="${ANDROID_FILES}.bak.$(date +%s)"
        mv "$ANDROID_FILES" "$backup"
        log "WARN" "Existing Android Files directory backed up to ${backup}"
    fi

    ln -sfn "$waydroid_shared" "$ANDROID_FILES"
    chown -h "$USER_UID:$USER_GID" "$ANDROID_FILES"

    log "INFO" "Android Files symlink created -> ${waydroid_shared}"
}

map_storage_directories() {
    log "INFO" "Mapping Android storage directories..."

    local waydroid_shared="${WAYDROID_DATA}/rootfs/media/0"
    local user_android="${ANDROID_FILES}"

    local mapped_dirs=(
        "Android/obb:obb"
        "Android/data:data"
        "DCIM:dcim"
        "Download:download"
        "Pictures:pictures"
        "Music:music"
        "Movies:movies"
        "Documents:documents"
    )

    for mapping in "${mapped_dirs[@]}"; do
        local android_path="${mapping%%:*}"
        local name="${mapping#*:}"
        local src="${waydroid_shared}/${android_path}"
        local dst="${user_android}/${android_path}"

        mkdir -p "$src"
        mkdir -p "$(dirname "$dst")"

        if [[ ! -L "$dst" ]] && [[ ! -d "$dst" ]]; then
            ln -sfn "$src" "$dst"
            chown -h "$USER_UID:$USER_GID" "$dst"
            log "INFO" "Mapped ${name} -> ${android_path}"
        fi
    done

    cat > "${user_android}/.aion-storage-map.json" << JSON_EOF
{
    "version": 1,
    "base": "${waydroid_shared}",
    "home": "${user_android}",
    "mappings": {
        "obb": "Android/obb",
        "data": "Android/data",
        "dcim": "DCIM",
        "download": "Download",
        "pictures": "Pictures",
        "music": "Music",
        "movies": "Movies",
        "documents": "Documents"
    }
}
JSON_EOF
    chmod 644 "${user_android}/.aion-storage-map.json"

    log "INFO" "Storage directories mapped"
}

create_systemd_services() {
    log "INFO" "Creating Waydroid systemd services..."

    cat > /etc/systemd/system/waydroid-container.service << CONTAINER_EOF
[Unit]
Description=Waydroid Container Service
After=network.target
Before=waydroid-session.service
Requires=binder-linux.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/waydroid container start
ExecStop=/usr/bin/waydroid container stop
TimeoutStartSec=60
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
CONTAINER_EOF

    cat > /etc/systemd/system/waydroid-session.service << SESSION_EOF
[Unit]
Description=Waydroid Session Service
After=waydroid-container.service graph-session.target
Requires=waydroid-container.service

[Service]
Type=simple
User=${USER_UID}
Group=${USER_GID}
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/${USER_UID}
ExecStart=/usr/bin/waydroid session start
ExecStop=/usr/bin/waydroid session stop
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SESSION_EOF

    cat > /etc/systemd/system/binder-linux.service << BINDER_EOF
[Unit]
Description=Binder Linux Module
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/sbin/modprobe binder_linux
ExecStop=/sbin/modprobe -r binder_linux
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
BINDER_EOF

    systemctl daemon-reload
    systemctl enable waydroid-container.service 2>>"$LOG_FILE" || true
    systemctl enable waydroid-session.service 2>>"$LOG_FILE" || true
    systemctl enable binder-linux.service 2>>"$LOG_FILE" || true

    log "INFO" "Systemd services created and enabled"
}

do_start() {
    log "INFO" "Starting Waydroid..."

    if systemctl is-active --quiet waydroid-container.service 2>/dev/null; then
        log "INFO" "Container already running"
    else
        systemctl start waydroid-container.service || die "Failed to start container"
        sleep 3
    fi

    if waydroid state info 2>/dev/null | grep -q "RUNNING"; then
        systemctl start waydroid-session.service || log "WARN" "Session service failed to start"
        log "INFO" "Waydroid started successfully"
    else
        die "Container failed to reach RUNNING state"
    fi
}

do_stop() {
    log "INFO" "Stopping Waydroid..."

    systemctl stop waydroid-session.service 2>>"$LOG_FILE" || true
    sleep 2
    systemctl stop waydroid-container.service 2>>"$LOG_FILE" || true

    if waydroid state info 2>/dev/null | grep -q "RUNNING"; then
        waydroid container stop 2>>"$LOG_FILE" || true
    fi

    log "INFO" "Waydroid stopped"
}

do_status() {
    echo "=== Aion Waydroid Status ==="
    echo ""

    if systemctl is-active --quiet binder-linux.service 2>/dev/null; then
        echo "Binder:      active"
    else
        echo "Binder:      inactive"
    fi

    if systemctl is-active --quiet waydroid-container.service 2>/dev/null; then
        echo "Container:   active"
    else
        echo "Container:   inactive"
    fi

    if systemctl is-active --quiet waydroid-session.service 2>/dev/null; then
        echo "Session:     active"
    else
        echo "Session:     inactive"
    fi

    if waydroid state info 2>/dev/null | grep -q "RUNNING"; then
        echo "State:       RUNNING"
    else
        echo "State:       STOPPED"
    fi

    echo ""
    if [[ -f "${WAYDROID_DATA}/waydroid.prop" ]]; then
        echo "=== Properties ==="
        grep -E "^persist\.|^ro\." "${WAYDROID_DATA}/waydroid.prop" 2>/dev/null || true
    fi

    if [[ -L "$ANDROID_FILES" ]]; then
        echo ""
        echo "Android Files: ${ANDROID_FILES} -> $(readlink -f "$ANDROID_FILES")"
    fi

    echo ""
    echo "=== GPU Config ==="
    if [[ -f "${WAYDROID_CFG}/gpu.conf" ]]; then
        cat "${WAYDROID_CFG}/gpu.conf"
    else
        echo "Not configured"
    fi
}

do_reset() {
    log "WARN" "Resetting Waydroid (this will delete all data)..."

    do_stop 2>>"$LOG_FILE" || true

    systemctl disable waydroid-container.service 2>>"$LOG_FILE" || true
    systemctl disable waydroid-session.service 2>>"$LOG_FILE" || true

    if [[ -d "$WAYDROID_DATA" ]]; then
        local backup="${WAYDROID_DATA}.bak.$(date +%s)"
        mv "$WAYDROID_DATA" "$backup"
        log "WARN" "Previous data backed up to ${backup}"
    fi

    if [[ -L "$ANDROID_FILES" ]]; then
        rm -f "$ANDROID_FILES"
    fi

    rm -f "${WAYDROID_CFG}/gpu.conf"
    rm -f /etc/systemd/system/waydroid-container.service
    rm -f /etc/systemd/system/waydroid-session.service
    rm -f /etc/systemd/system/binder-linux.service

    systemctl daemon-reload

    log "INFO" "Waydroid reset complete"
    echo "Waydroid has been reset. Run 'waydroid-init.sh --start' to reinitialize."
}

usage() {
    cat << USAGE_EOF
Aion Waydroid Init Script

Usage: $(basename "$0") [OPTIONS]

Options:
  --init [TYPE]   Initialize Waydroid container (GAPPS or VANILLA, default: VANILLA)
  --start         Start Waydroid container and session
  --stop          Stop Waydroid session and container
  --status        Show Waydroid status
  --reset         Reset Waydroid (deletes all data, backs up first)
  --full-setup    Run complete first-time setup (deps + init + configure + start)
  --help          Show this help message

Examples:
  $(basename "$0") --full-setup
  $(basename "$0") --init GAPPS
  $(basename "$0") --start
  $(basename "$0") --status

Log file: ${LOG_FILE}
USAGE_EOF
}

main() {
    local action=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --init)
                action="init"
                IMAGE_TYPE="${2:-VANILLA}"
                shift 2 || shift
                ;;
            --start)
                action="start"
                shift
                ;;
            --stop)
                action="stop"
                shift
                ;;
            --status)
                action="status"
                shift
                ;;
            --reset)
                action="reset"
                shift
                ;;
            --full-setup)
                action="full"
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
    done

    if [[ -z "$action" ]]; then
        usage
        exit 1
    fi

    log "INFO" "=== Aion Waydroid Init - Action: ${action} ==="

    case "$action" in
        full)
            check_root
            detect_distro
            install_dependencies
            ensure_binder_module
            initialize_container "${IMAGE_TYPE:-VANILLA}"
            configure_headless
            configure_gpu_rendering
            configure_persistent_storage
            setup_android_files_symlink
            map_storage_directories
            create_systemd_services
            do_start
            log "INFO" "=== Full setup complete ==="
            echo "Aion Waydroid setup complete. Android Files available at: ${ANDROID_FILES}"
            ;;
        init)
            check_root
            detect_distro
            install_dependencies
            ensure_binder_module
            initialize_container "${IMAGE_TYPE:-VANILLA}"
            configure_headless
            configure_gpu_rendering
            configure_persistent_storage
            setup_android_files_symlink
            map_storage_directories
            create_systemd_services
            log "INFO" "=== Init complete ==="
            ;;
        start)
            do_start
            ;;
        stop)
            do_stop
            ;;
        status)
            do_status
            ;;
        reset)
            check_root
            do_reset
            ;;
    esac
}

main "$@"
