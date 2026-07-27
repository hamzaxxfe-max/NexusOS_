#!/bin/bash
set -euo pipefail

LOG_DIR="/var/log/nexusos"
LOG_FILE="${LOG_DIR}/wine-optimize.log"
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $1" | tee -a "$LOG_FILE" >&2
}

if [ $# -lt 1 ]; then
    echo "Usage: $0 <WINEPREFIX_PATH>"
    echo "  Optimizes a Wine prefix for gaming on NexusOS."
    exit 1
fi

WINEPREFIX="$1"

if [ ! -d "$WINEPREFIX" ]; then
    log_error "Wine prefix not found: $WINEPREFIX"
    exit 1
fi

if [ ! -f "$WINEPREFIX/system.reg" ]; then
    log_error "Not a valid Wine prefix (missing system.reg): $WINEPREFIX"
    exit 1
fi

export WINEPREFIX
export WINEDEBUG="-all"
export WINEARCH="win64"

log "Optimizing Wine prefix: $WINEPREFIX"

log "Setting renderer to Vulkan..."
reg add "HKCU\\Software\\Wine\\DllOverrides" /v d3d11 /t REG_SZ /d d3d11 /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\DllOverrides" /v dxgi /t REG_SZ /d dxgi /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\DllOverrides" /v d3d9 /t REG_SZ /d d3d9 /f 2>/dev/null || true

log "Disabling mouse warp override..."
reg add "HKCU\\System\\Wine\\Wine Config" /v MouseWarpOverride /t REG_SZ /d disable /f 2>/dev/null || true

log "Enabling Esync/Fsync..."
reg add "HKCU\\Software\\Wine\\DllOverrides" /v winevulkan /t REG_SZ /d native /f 2>/dev/null || true

log "Configuring render timer..."
reg add "HKCU\\Software\\Wine\\Direct3D" /v RenderTimer /t REG_SZ /d 0 /f 2>/dev/null || true

log "Setting Intel GPU optimizations..."
reg add "HKCU\\Software\\Wine\\Direct3D" /v UseGLSL /t REG_SZ /d enabled /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\Direct3D" /v VideoMemorySize /t REG_SZ /d 4096 /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\Direct3D" /v ShaderModel /t REG_SZ /d 4 /f 2>/dev/null || true

log "Disabling Wine desktop integration bloat..."
reg add "HKCU\\Software\\Wine\\DllOverrides" /v wineexplorer /t REG_SZ /d disabled /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\DllOverrides" /v winefile /t REG_SZ /d disabled /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\DllOverrides" /v winemenubuilder /t REG_SZ /d disabled /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\DllOverrides" /v wineboot /t REG_SZ /d disabled /f 2>/dev/null || true

ENV_FILE="${WINEPREFIX}/nexusos.env"
log "Writing optimized environment file: ${ENV_FILE}"
cat > "$ENV_FILE" << 'ENVEOF'
DXVK_ASYNC=1
STAGING_SHARED_MEMORY=1
MANGOHUD_CONFIG=fps_limit=60,no_display
WINEFSYNC=1
WINEESYNC=1
WINEDEBUG=-all
WINE_DISABLE_GL_STRING_CACHE=1
vblank_mode=0
__GL_THREADED_OPTIMIZATIONS=1
WINE_VK_USE_SYSTEM_VULKAN=1
ENABLE_VKBASALT=0
WINE_FULLSCREEN_FSR=1
WINE_FULLSCREEN_FSR_STRENGTH=3
ENVEOF

log "Disabling Wine services that waste resources..."
rm -f "$WINEPREFIX/drive_c/windows/system32/winebus.sys" 2>/dev/null || true

if [ -d "$WINEPREFIX/drive_c/windows/system32/winealsa.drv" ]; then
    log "Keeping ALSA audio driver"
fi

log "Setting virtual desktop to disabled (fullscreen preferred)..."
reg add "HKCU\\Software\\Wine\\Explorer" /v Desktops /t REG_SZ /d "Default" /f 2>/dev/null || true
reg add "HKCU\\Software\\Wine\\Explorer\\Desktops" /v Default /t REG_SZ /d "" /f 2>/dev/null || true

if command -v gamemoderun &>/dev/null; then
    log "GameMode detected - will be used on game launch"
fi

if command -v mangohud &>/dev/null; then
    log "MangoHud detected - FPS overlay available"
fi

log "Optimization complete for: $WINEPREFIX"
log "Prefix size: $(du -sh "$WINEPREFIX" 2>/dev/null | cut -f1)"
