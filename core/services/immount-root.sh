#!/bin/bash
set -euo pipefail

# Single source of truth for subvolume names + label.
CONSTANTS="/usr/lib/aion/constants.sh"
[[ -f "${CONSTANTS}" ]] && . "${CONSTANTS}"

AION_ROOT_MOUNT="/mnt/aion-root"
OVERLAY_UPPER="/run/aion/overlay-upper"
OVERLAY_WORK="/run/aion/overlay-work"
OVERLAY_MERGED="/run/aion/overlay-merged"
BTRFS_DEVID=""
MAINTENANCE_FLAG="/etc/aion/.maintenance-mode"
LOG_FILE="/var/log/aion/immutable-root.log"
ROLLBACK_FLAG="/etc/aion/.rollback-requested"
ROOT_SUBVOL="${SUBVOL_ROOT:-@}"
HOME_SUBVOL="${SUBVOL_HOME:-@home}"
VAR_SUBVOL="${SUBVOL_VAR:-@var}"
TMP_SUBVOL="${SUBVOL_TMP:-@tmp}"
ETC_SUBVOL="${SUBVOL_ETC:-@etc-aion}"

log() {
    local level="$1"
    shift
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] $*" | tee -a "${LOG_FILE}" 2>/dev/null || echo "[${timestamp}] [${level}] $*"
}

log_info() { log "INFO" "$@"; }
log_warn() { log "WARN" "$@"; }
log_error() { log "ERROR" "$@"; }

cleanup() {
    local exit_code=$?
    if [ ${exit_code} -ne 0 ]; then
        log_error "Script exited with error code ${exit_code}"
    fi
    return ${exit_code}
}
trap cleanup EXIT

check_prerequisites() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "This script must be run as root"
        exit 1
    fi

    if ! command -v btrfs &>/dev/null; then
        log_error "btrfs-progs not found"
        exit 1
    fi

    if ! command -v mount.overlay &>/dev/null && ! modprobe overlay 2>/dev/null; then
        log_warn "overlay module not available, attempting modprobe"
        if ! modprobe overlay 2>/dev/null; then
            log_error "Cannot load overlay kernel module"
            exit 1
        fi
    fi

    mkdir -p "$(dirname "${LOG_FILE}")"
}

detect_root_device() {
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)
    if [ -z "${root_dev}" ]; then
        log_error "Cannot detect root device"
        exit 1
    fi

    if ! btrfs filesystem show "${root_dev}" &>/dev/null; then
        log_error "${root_dev} is not a Btrfs filesystem"
        exit 1
    fi

    BTRFS_DEVID=$(btrfs filesystem show "${root_dev}" | grep -oP 'devid \K[0-9]+' | head -1)
    log_info "Root device: ${root_dev} (BTRFS_DEVID=${BTRFS_DEVID})"
}

check_maintenance_mode() {
    if [ -f "${MAINTENANCE_FLAG}" ]; then
        log_info "Maintenance mode active, skipping immutable root setup"
        rm -f "${MAINTENANCE_FLAG}"
        return 1
    fi
    return 0
}

check_rollback() {
    if [ -f "${ROLLBACK_FLAG}" ]; then
        log_info "Rollback requested, reverting to mutable root"
        rm -f "${ROLLBACK_FLAG}"
        perform_rollback
        exit 0
    fi
}

create_subvolumes() {
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)
    local mount_opts="rw,compress=zstd:3,noatime,ssd,discard=async"

    mkdir -p "${AION_ROOT_MOUNT}"

    local existing_subs
    existing_subs=$(btrfs subvolume list "${root_dev}" 2>/dev/null || true)

    if echo "${existing_subs}" | grep -q " ${ROOT_SUBVOL}"; then
        log_info "Subvolume ${ROOT_SUBVOL} already exists"
    else
        log_info "Creating subvolume ${ROOT_SUBVOL}"
        btrfs subvolume create "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}"
    fi

    local subvols=("${HOME_SUBVOL}" "${ETC_SUBVOL}" "${VAR_SUBVOL}" "${TMP_SUBVOL}" "${ROOT_SUBVOL}")
    for subvol in "${subvols[@]}"; do
        if echo "${existing_subs}" | grep -q " ${subvol}$"; then
            log_info "Subvolume ${subvol} already exists"
        else
            log_info "Creating subvolume ${subvol}"
            btrfs subvolume create "${AION_ROOT_MOUNT}/${subvol}"
        fi
    done
}

populate_root_subvolume() {
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)

    log_info "Populating ${ROOT_SUBVOL} subvolume with current root contents"
    btrfs subvolume set-default "$(btrfs subvolume show "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}" | grep 'Subvolume ID' | awk '{print $3}')" "${root_dev}" 2>/dev/null || true

    local temp_mount="/tmp/aion-populate-$$"
    mkdir -p "${temp_mount}"
    mount -t btrfs -o rw,compress=zstd:3,noatime "${root_dev}" "${temp_mount}"

    if [ ! -f "${temp_mount}/${ROOT_SUBVOL}/.populated" ]; then
        rsync -a --exclude="/${ROOT_SUBVOL}/*" --exclude="/${HOME_SUBVOL}/*" --exclude="/${ETC_SUBVOL}/*" \
            --exclude="/${VAR_SUBVOL}/*" --exclude="/${TMP_SUBVOL}/*" --exclude='/boot/*' \
            "${temp_mount}/" "${temp_mount}/${ROOT_SUBVOL}/" 2>/dev/null || \
            cp -a "${temp_mount}/"[!.]* "${temp_mount}/${ROOT_SUBVOL}/" 2>/dev/null || true
        touch "${temp_mount}/${ROOT_SUBVOL}/.populated"
        log_info "Root subvolume populated"
    fi

    umount "${temp_mount}"
    rmdir "${temp_mount}"
}

set_readonly_root() {
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)

    local subvol_id
    subvol_id=$(btrfs subvolume show "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}" | grep 'Subvolume ID' | awk '{print $3}')

    log_info "Setting ${ROOT_SUBVOL} subvolume to read-only (id=${subvol_id})"
    btrfs subvolume set-default "${subvol_id}" "${root_dev}"
    chattr +i "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}" 2>/dev/null || true
}

setup_overlay_dirs() {
    log_info "Setting up overlay directories"
    mkdir -p "${OVERLAY_UPPER}"
    mkdir -p "${OVERLAY_WORK}"
    mkdir -p "${OVERLAY_MERGED}"

    mount -t tmpfs -o size=2G,nosuid,nodev,noexec tmpfs "${OVERLAY_UPPER}" || {
        log_error "Failed to mount tmpfs for overlay upper"
        exit 1
    }

    mkdir -p "${OVERLAY_UPPER}/upper"
    mkdir -p "${OVERLAY_UPPER}/work"
}

setup_systemd_tmpfiles() {
    log_info "Configuring systemd-tmpfiles for /tmp and /var/tmp"

    local tmpfiles_conf="/etc/tmpfiles.d/aion-immutable.conf"
    mkdir -p /etc/tmpfiles.d

    cat > "${tmpfiles_conf}" << 'TMPFILES_EOF'
# Aion Immutable Root - Temporary file configuration
# Type Path Mode User Group Age Argument
d /tmp 1777 root root 10d -
d /var/tmp 1777 root root 30d -
d /run/aion 0755 root root - -
d /run/aion/overlay-upper 0755 root root - -
d /run/aion/overlay-work 0755 root root - -
d /run/aion/overlay-merged 0755 root root - -
d /var/log/aion 0755 root root - -
d /etc/aion 0755 root root - -
TMPFILES_EOF

    log_info "tmpfiles configuration written to ${tmpfiles_conf}"
}

create_systemd_services() {
    log_info "Creating systemd service files for overlay mounting"

    local service_dir="/etc/systemd/system"
    mkdir -p "${service_dir}"

    cat > "${service_dir}/aion-overlay.service" << 'SERVICE_EOF'
[Unit]
Description=Aion Immutable Root Overlay Setup
DefaultDependencies=no
After=local-fs.target
Before=systemd-tmpfiles-setup.service
RequiresMountsFor=/run/aion

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/lib/aion/immount-root.sh --mount-overlay
ExecStop=/usr/lib/aion/immount-root.sh --unmount-overlay
ExecReload=/usr/lib/aion/immount-root.sh --remount-overlay
TimeoutStartSec=60
TimeoutStopSec=30

[Install]
WantedBy=local-fs.target
SERVICE_EOF

    cat > "${service_dir}/aion-validate.service" << 'VALIDATE_EOF'
[Unit]
Description=Aion Btrfs Filesystem Validation
After=aion-overlay.service
Before=aion-games.target

[Service]
Type=oneshot
ExecStart=/usr/lib/aion/immount-root.sh --validate
ExecStartPre=/usr/lib/aion/immount-root.sh --check-maintenance
TimeoutStartSec=120

[Install]
WantedBy=aion-games.target
VALIDATE_EOF

    systemctl daemon-reload
    log_info "Systemd services created and daemon reloaded"
}

mount_overlay() {
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)

    local root_subvol_id
    root_subvol_id=$(btrfs subvolume show "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}" | grep 'Subvolume ID' | awk '{print $3}')

    local root_lower="${AION_ROOT_MOUNT}/${ROOT_SUBVOL}"

    setup_overlay_dirs

    log_info "Mounting overlayfs: lower=${root_lower}, upper=${OVERLAY_UPPER}/upper, work=${OVERLAY_UPPER}/work"

    mount -t overlay overlay \
        -o "lowerdir=${root_lower},upperdir=${OVERLAY_UPPER}/upper,workdir=${OVERLAY_UPPER}/work" \
        "${OVERLAY_MERGED}" || {
        log_error "Failed to mount overlay filesystem"
        return 1
    }

    log_info "Overlay mounted at ${OVERLAY_MERGED}"

    if mountpoint -q /; then
        log_info "Root is already the overlay mount point"
    else
        log_info "Overlay prepared at ${OVERLAY_MERGED} (switch_root available if needed)"
    fi
}

unmount_overlay() {
    if mountpoint -q "${OVERLAY_MERGED}" 2>/dev/null; then
        log_info "Unmounting overlay at ${OVERLAY_MERGED}"
        umount -l "${OVERLAY_MERGED}" || {
            log_warn "Lazy unmount failed, trying forced unmount"
            umount -f "${OVERLAY_MERGED}" || log_error "Failed to unmount overlay"
        }
    fi

    if mountpoint -q "${OVERLAY_UPPER}" 2>/dev/null; then
        umount -l "${OVERLAY_UPPER}" || true
    fi

    log_info "Overlay unmounted"
}

remount_overlay() {
    unmount_overlay
    sleep 1
    mount_overlay
    log_info "Overlay remounted successfully"
}

validate_btrfs() {
    log_info "Validating Btrfs filesystem integrity"
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)

    log_info "Running btrfs check on ${root_dev}"
    if btrfs check --readonly "${root_dev}" 2>&1; then
        log_info "Btrfs check passed"
    else
        log_error "Btrfs check detected issues on ${root_dev}"
        return 1
    fi

    log_info "Scrubbing Btrfs filesystem"
    if btrfs scrub start -B "${root_dev}" 2>&1; then
        log_info "Btrfs scrub completed"
    else
        log_warn "Btrfs scrub reported issues"
    fi

    local alloc_info
    alloc_info=$(btrfs filesystem df "${root_dev}" 2>/dev/null || true)
    log_info "Filesystem allocation:\n${alloc_info}"

    log_info "Btrfs validation complete"
}

perform_rollback() {
    log_info "Performing rollback to mutable root"
    local root_dev
    root_dev=$(findmnt -n -o SOURCE /)

    unmount_overlay

    log_info "Resetting default subvolume to top-level"
    btrfs subvolume set-default 0 "${root_dev}"

    chattr -i "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}" 2>/dev/null || true

    log_info "Rollback complete. Root is now mutable."
    log_info "Reboot recommended to apply changes."
}

enable_maintenance_mode() {
    mkdir -p "$(dirname "${MAINTENANCE_FLAG}")"
    touch "${MAINTENANCE_FLAG}"
    log_info "Maintenance mode enabled. Reboot to get mutable root."
}

disable_maintenance_mode() {
    rm -f "${MAINTENANCE_FLAG}"
    log_info "Maintenance mode disabled."
}

print_status() {
    echo "=== Aion Immutable Root Status ==="
    echo ""

    local root_dev
    root_dev=$(findmnt -n -o SOURCE / 2>/dev/null || echo "unknown")
    echo "Root device: ${root_dev}"

    if mountpoint -q "${OVERLAY_MERGED}" 2>/dev/null; then
        echo "Overlay: ACTIVE (mounted at ${OVERLAY_MERGED})"
    else
        echo "Overlay: INACTIVE"
    fi

    if [ -f "${MAINTENANCE_FLAG}" ]; then
        echo "Maintenance mode: ENABLED"
    else
        echo "Maintenance mode: DISABLED"
    fi

    local root_subvol
    root_subvol=$(btrfs subvolume show "${AION_ROOT_MOUNT}/${ROOT_SUBVOL}" 2>/dev/null | grep 'Name' | awk '{print $2}' || echo "unknown")
    echo "Root subvolume: ${root_subvol}"

    local default_subvol
    default_subvol=$(btrfs subvolume get-default "${root_dev}" 2>/dev/null | awk '{print $1}' || echo "unknown")
    echo "Default subvolume ID: ${default_subvol}"

    echo ""
    echo "Subvolumes:"
    btrfs subvolume list "${root_dev}" 2>/dev/null || echo "  (unable to list)"
}

usage() {
    cat << EOF
Usage: $(basename "$0") [OPTION]

Aion Immutable Root Setup Script

Options:
  --setup           First-boot setup: create subvolumes, configure overlay
  --mount-overlay   Mount the overlay filesystem
  --unmount-overlay Unmount the overlay filesystem
  --remount-overlay Remount the overlay (for updates)
  --validate        Validate Btrfs filesystem integrity
  --rollback        Revert to mutable root
  --maintenance     Enable maintenance mode (skip immutable root)
  --no-maintenance  Disable maintenance mode
  --status          Show current immutable root status
  --check-maintenance  Check if maintenance mode is active (exit 1 if yes)
  --help            Show this help message

EOF
}

main() {
    check_prerequisites

    case "${1:-}" in
        --setup)
            log_info "=== Aion Immutable Root First Boot Setup ==="
            check_maintenance_mode || exit 0
            check_rollback
            detect_root_device
            create_subvolumes
            populate_root_subvolume
            set_readonly_root
            setup_overlay_dirs
            setup_systemd_tmpfiles
            create_systemd_services
            log_info "=== Setup complete ==="
            ;;
        --mount-overlay)
            log_info "Mounting overlay"
            detect_root_device
            mount_overlay
            ;;
        --unmount-overlay)
            log_info "Unmounting overlay"
            unmount_overlay
            ;;
        --remount-overlay)
            log_info "Remounting overlay"
            remount_overlay
            ;;
        --validate)
            detect_root_device
            validate_btrfs
            ;;
        --rollback)
            detect_root_device
            perform_rollback
            ;;
        --maintenance)
            enable_maintenance_mode
            ;;
        --no-maintenance)
            disable_maintenance_mode
            ;;
        --status)
            print_status
            ;;
        --check-maintenance)
            check_maintenance_mode && exit 0 || exit 1
            ;;
        --help)
            usage
            ;;
        "")
            log_error "No option specified"
            usage
            exit 1
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
}

main "$@"
