#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG_FILE="/var/log/aion/gpu-autodetect.log"
UDEV_RULES="/etc/udev/rules.d/80-aion-gpu.rules"
MKINITCPIO_CONF="/etc/mkinitcpio.conf"
XORG_CONF="/etc/X11/xorg.conf.d/10-aion-gpu.conf"
WAYLAND_ENV="/etc/aion/wayland-hybrid.env"

mkdir -p "$(dirname "$LOG_FILE")" /etc/X11/xorg.conf.d /etc/aion /etc/modprobe.d

log()    { echo -e "${CYAN}[GPU]${NC} $*"; echo "[GPU] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_ok() { echo -e "${GREEN}[GPU]${NC} $*"; echo "[GPU] OK $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn(){ echo -e "${YELLOW}[GPU]${NC} $*"; echo "[GPU] WARN $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_err(){ echo -e "${RED}[GPU]${NC} $*"; echo "[GPU] ERR $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

NVIDIA_FOUND=false
AMD_FOUND=false
INTEL_FOUND=false
NVIDIA_BDF=""
INTEL_BDF=""
AMD_BDF=""
GPU_COUNT=0

detect_gpus() {
    log "Scanning PCI bus for GPUs..."

    while IFS= read -r pci_path; do
        [ ! -f "$pci_path/vendor" ] && continue
        vendor=$(cat "$pci_path/vendor" 2>/dev/null || continue)
        class=$(cat "$pci_path/class" 2>/dev/null || echo "")
        bdf=$(basename "$(dirname "$pci_path")")

        case "$class" in
            0x030000|0x030200) ;;
            *) continue ;;
        esac

        case "$vendor" in
            0x10de)
                NVIDIA_FOUND=true
                NVIDIA_BDF="$bdf"
                log "NVIDIA GPU detected: PCI $bdf"
                ;;
            0x1002)
                AMD_FOUND=true
                AMD_BDF="$bdf"
                log "AMD GPU detected: PCI $bdf"
                ;;
            0x8086)
                INTEL_FOUND=true
                INTEL_BDF="$bdf"
                log "Intel GPU detected: PCI $bdf"
                ;;
        esac
    done < <(find /sys/bus/pci/devices -maxdepth 1 -type l 2>/dev/null)

    $NVIDIA_FOUND && GPU_COUNT=$((GPU_COUNT + 1))
    $AMD_FOUND    && GPU_COUNT=$((GPU_COUNT + 1))
    $INTEL_FOUND  && GPU_COUNT=$((GPU_COUNT + 1))

    log "Total GPUs detected: $GPU_COUNT"
}

blacklist_nouveau() {
    if ! $NVIDIA_FOUND; then
        return
    fi

    log "NVIDIA GPU found — blacklisting nouveau driver"

    cat > /etc/modprobe.d/aion-nvidia-blacklist.conf << 'EOF'
# Aion: blacklist nouveau in favor of proprietary NVIDIA driver
# nouveau causes black screen on Wayland + Gamescope
blacklist nouveau
options nouveau modeset=0
EOF

    if lsmod 2>/dev/null | grep -q "^nouveau"; then
        log_warn "nouveau currently loaded — unloading"
        modprobe -r nouveau 2>/dev/null || log_warn "Could not unload nouveau (in use)"
    fi

    log_ok "nouveau blacklisted"
}

configure_nvidia_precompiled() {
    log "Configuring NVIDIA pre-compiled drivers (no dkms)"

    cat > /etc/modprobe.d/aion-nvidia.conf << 'EOF'
# Aion NVIDIA Configuration
# DRM modeset for Wayland + Gamescope
options nvidia-drm modeset=1
options nvidia-drm fbdev=1

# Power management
options nvidia NVreg_PreserveVideoMemoryAllocations=1
options nvidia NVreg_TemporaryFilePath=/var/tmp

# Disable GVO for consistent frame pacing
options nvidia NVreg_EnableGpuFirmware=0
EOF

    log_ok "NVIDIA modprobe options written"

    systemctl enable nvidia-suspend.service 2>/dev/null || true
    systemctl enable nvidia-resume.service 2>/dev/null || true
    systemctl enable nvidia-hibernate.service 2>/dev/null || true
    log_ok "NVIDIA suspend/resume services enabled"
}

update_mkinitcpio() {
    if [ ! -f "$MKINITCPIO_CONF" ]; then
        log_warn "mkinitcpio.conf not found — skipping MODULES update"
        return
    fi

    log "Updating mkinitcpio.conf MODULES"

    MODULES_TO_ADD=()

    if $NVIDIA_FOUND; then
        MODULES_TO_ADD+=("nvidia" "nvidia_modeset" "nvidia_uvm" "nvidia_drm")
    fi

    if $AMD_FOUND; then
        MODULES_TO_ADD+=("amdgpu")
    fi

    if $INTEL_FOUND && ! $NVIDIA_FOUND; then
        MODULES_TO_ADD+=("i915")
    fi

    if [ ${#MODULES_TO_ADD[@]} -eq 0 ]; then
        log "No GPU modules to add"
        return
    fi

    current_modules=$(grep "^MODULES=" "$MKINITCPIO_CONF" | sed 's/^MODULES=(//' | sed 's/)$//' || echo "")

    for mod in "${MODULES_TO_ADD[@]}"; do
        if echo "$current_modules" | grep -qw "$mod"; then
            log "Module $mod already in MODULES"
        else
            if [ -z "$current_modules" ]; then
                current_modules="$mod"
            else
                current_modules="$current_modules $mod"
            fi
            log "Added module: $mod"
        fi
    done

    sed -i "s/^MODULES=.*/MODULES=($current_modules)/" "$MKINITCPIO_CONF"

    log_ok "mkinitcpio.conf updated with GPU modules: ${MODULES_TO_ADD[*]}"
}

rebuild_initramfs() {
    log "Rebuilding initramfs for GPU module changes"

    if command -v mkinitcpio &>/dev/null; then
        if mkinitcpio -P 2>&1 | tee -a "$LOG_FILE"; then
            log_ok "initramfs rebuilt successfully"
        else
            log_err "initramfs rebuild failed"
            return 1
        fi
    elif command -v dracut &>/dev/null; then
        if dracut --force --regenerate-all 2>&1 | tee -a "$LOG_FILE"; then
            log_ok "initramfs rebuilt via dracut"
        else
            log_err "dracut rebuild failed"
            return 1
        fi
    else
        log_warn "No initramfs generator found (mkinitcpio/dracut)"
    fi
}

write_udev_rules() {
    log "Writing udev rules for GPU power management"

    cat > "$UDEV_RULES" << 'UDEV_EOF'
# Aion GPU udev rules — auto-generated by gpu-autodetect.sh

# NVIDIA RTD3 power management
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030200", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="unbind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="on"
ACTION=="unbind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030200", TEST=="power/control", ATTR{power/control}="on"

# AMD GPU power management
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x1002", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="unbind", SUBSYSTEM=="pci", ATTR{vendor}=="0x1002", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="on"

# Thunderbolt auto-authorize for eGPU hotplug
ACTION=="add", SUBSYSTEM=="thunderbolt", ATTR{authorized}=="0", ATTR{authorized}="1"
UDEV_EOF

    chmod 644 "$UDEV_RULES"
    udevadm control --reload-rules 2>/dev/null || true
    log_ok "udev rules written to $UDEV_RULES"
}

load_nvidia_modules() {
    log "Loading NVIDIA kernel modules"

    if ! $NVIDIA_FOUND; then
        return
    fi

    if [ -d "/usr/lib/modules" ]; then
        if find /usr/lib/modules -name "nvidia.ko*" -print -quit 2>/dev/null | grep -q .; then
            log "Pre-compiled NVIDIA modules found in /usr/lib/modules"
        else
            log_warn "No NVIDIA modules found in /usr/lib/modules"
            log_warn "Run: pacman -S nvidia-cachyos-bore to install pre-compiled drivers"
            return 1
        fi
    fi

    modprobe nvidia NVreg_PreserveVideoMemoryAllocations=1 2>/dev/null || true
    modprobe nvidia_modeset 2>/dev/null || true
    modprobe nvidia_drm modeset=1 2>/dev/null || true
    modprobe nvidia_uvm 2>/dev/null || true

    if lsmod 2>/dev/null | grep -q "^nvidia"; then
        log_ok "NVIDIA modules loaded successfully"
    else
        log_warn "NVIDIA modules not loaded (will load on next boot via initramfs)"
    fi
}

load_amd_modules() {
    if ! $AMD_FOUND; then
        return
    fi

    log "Loading AMD GPU modules"
    if ! lsmod 2>/dev/null | grep -q "^amdgpu"; then
        modprobe amdgpu 2>/dev/null || true
    fi
    log_ok "AMD GPU modules ready"
}

load_intel_modules() {
    if ! $INTEL_FOUND; then
        return
    fi

    log "Loading Intel GPU modules"
    if ! lsmod 2>/dev/null | grep -q "^i915"; then
        modprobe i915 2>/dev/null || true
    fi
    log_ok "Intel GPU modules ready"
}

write_xorg_hybrid_config() {
    if ! $NVIDIA_FOUND || ! $INTEL_FOUND; then
        return
    fi

    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        log "Display server already running — skipping Xorg config"
        return
    fi

    log "Writing Xorg hybrid GPU config (Intel + NVIDIA)"

    local intel_busid="PCI:0:2:0"
    local nvidia_busid=""
    if [ -n "$NVIDIA_BDF" ]; then
        nvidia_busid=$(echo "$NVIDIA_BDF" | sed 's/\([0-9a-f]*\):\([0-9a-f]*\)\.\([0-9a-f]*\)/PCI:\1:\2/')
    else
        nvidia_busid="PCI:1:0:0"
    fi

    cat > "$XORG_CONF" << XEOF
# Aion auto-generated hybrid GPU config
# Primary: Intel iGPU | Secondary: NVIDIA dGPU (PRIME offload)
Section "ServerLayout"
    Identifier "aion-hybrid"
    Screen 0 "intel"
    Screen 1 "nvidia" RightOf "intel"
    Option "Xinerama" "0"
EndSection

Section "Device"
    Identifier "intel"
    Driver "intel"
    BusID "${intel_busid}"
    Option "AccelMethod" "sna"
    Option "TearFree" "true"
    Option "DRI" "3"
EndSection

Section "Screen"
    Identifier "intel"
    Device "intel"
    DefaultDepth 24
EndSection

Section "Device"
    Identifier "nvidia"
    Driver "nvidia"
    BusID "${nvidia_busid}"
    Option "AllowEmptyInitialConfiguration"
    Option "Coolbits" "28"
    Option "TripleBuffer" "true"
EndSection

Section "Screen"
    Identifier "nvidia"
    Device "nvidia"
    DefaultDepth 24
EndSection
XEOF

    log_ok "Xorg config written to $XORG_CONF"
}

write_wayland_hybrid_env() {
    if ! $NVIDIA_FOUND; then
        return
    fi

    log "Writing Wayland hybrid environment variables"

    cat > "$WAYLAND_ENV" << 'EOF'
# Aion Wayland hybrid GPU environment
# Render on NVIDIA dGPU, display on Intel iGPU
__NV_PRIME_RENDER_OFFLOAD=1
__GLX_VENDOR_LIBRARY_NAME=nvidia
GBM_BACKEND=nvidia-drm
__GL_GMOD_ALLOWED_INTEL=0
NVIDIA_DRIVER_CAPABILITIES=all
LIBVA_DRIVER_NAME=nvidia
EOF

    local profile_dropin="/etc/profile.d/aion-wayland-hybrid.sh"
    cat > "$profile_dropin" << 'PEOF'
[ -f /etc/aion/wayland-hybrid.env ] && set -a && . /etc/aion/wayland-hybrid.env && set +a
PEOF
    chmod 644 "$profile_dropin"

    log_ok "Wayland env written to $WAYLAND_ENV"
}

print_summary() {
    echo ""
    echo "=============================================="
    echo "  GPU Auto-Detection Summary"
    echo "=============================================="
    echo ""
    echo "  NVIDIA: $($NVIDIA_FOUND && echo 'YES (PCI '$NVIDIA_BDF')' || echo 'no')"
    echo "  AMD:    $($AMD_FOUND && echo 'YES (PCI '$AMD_BDF')' || echo 'no')"
    echo "  Intel:  $($INTEL_FOUND && echo 'YES (PCI '$INTEL_BDF')' || echo 'no')"
    echo "  Total:  $GPU_COUNT GPU(s)"
    echo ""
    echo "  Configurations applied:"
    $NVIDIA_FOUND && echo "    - NVIDIA: pre-compiled drivers (no dkms)"
    $NVIDIA_FOUND && echo "    - NVIDIA: DRM modeset=1 + fbdev=1"
    $NVIDIA_FOUND && echo "    - nouveau: blacklisted"
    $NVIDIA_FOUND && $INTEL_FOUND && echo "    - Hybrid: Intel+NVIDIA PRIME offload"
    $AMD_FOUND    && echo "    - AMD: amdgpu + Vulkan RADV"
    $INTEL_FOUND  && echo "    - Intel: i915 + DRI3"
    echo "    - udev: power management rules"
    echo "    - mkinitcpio: GPU modules added"
    echo ""
    echo "=============================================="
}

main() {
    log "=== Aion GPU Auto-Detection ==="

    detect_gpus

    if [ "$GPU_COUNT" -eq 0 ]; then
        log_warn "No GPU detected — system will use software rendering"
        print_summary
        return 0
    fi

    blacklist_nouveau

    if $NVIDIA_FOUND; then
        configure_nvidia_precompiled
    fi

    update_mkinitcpio
    rebuild_initramfs

    load_nvidia_modules
    load_amd_modules
    load_intel_modules

    write_udev_rules
    write_xorg_hybrid_config
    write_wayland_hybrid_env

    print_summary

    log "=== GPU Auto-Detection complete ==="
}

main "$@"
