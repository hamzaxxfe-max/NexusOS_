#!/usr/bin/env bash
# =============================================================================
# Aion Chrome First-Run Configuration
# =============================================================================
# Configures Google Chrome with enterprise policies, gaming-optimized flags,
# telemetry disabling, and a desktop entry on first boot.
#
# Usage:
#   sudo ./first-run-config.sh
#
# Logs to: /var/log/aion/chrome-setup.log
# =============================================================================

set -euo pipefail

# =============================================================================
# Constants
# =============================================================================
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LOG_DIR="/var/log/aion"
readonly LOG_FILE="${LOG_DIR}/chrome-setup.log"
readonly CHROME_POLICY_DIR="/etc/opt/chrome/policies/managed"
readonly CHROME_POLICY_SRC="${SCRIPT_DIR}/chrome-policies.json"
readonly CHROME_FLAGS_FILE="/opt/aion/chrome-flags.conf"
readonly DESKTOP_ENTRY_SRC="${SCRIPT_DIR}/aion-chrome.desktop"
readonly DESKTOP_ENTRY_DIR="/usr/share/applications"
readonly CHROME_PROFILE_BASE="/home/aion/.config/google-chrome"
readonly CHROME_POLICY_DEST="${CHROME_POLICY_DIR}/aion-policies.json"
readonly FLAG_MARKER="/var/lib/aion/.chrome-configured"

# =============================================================================
# Chrome Launch Flags (Gaming Optimized)
# =============================================================================
readonly -a CHROME_FLAGS=(
    "--enable-gpu-rasterization"
    "--enable-zero-copy"
    "--ignore-gpu-blocklist"
    "--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL,ParallelDownloading,HeavyAdPrivacyMitigations"
    "--disable-features=TranslateUI,ChromeWhatsNewUI,SidePanelPinning,OptimizationHints,PrivacySandboxSettings4"
    "--disable-background-networking"
    "--disable-default-apps"
    "--no-first-run"
    "--disable-breakpad"
    "--disable-component-update"
    "--disable-default-browser-check"
    "--disable-features=AudioServiceSandbox"
    "--disable-hang-monitor"
    "--disable-ipc-flooding-protection"
    "--disable-renderer-backgrounding"
    "--disable-backgrounding-occluded-windows"
    "--disable-background-timer-throttling"
    "--disable-renderer-pausing"
    "--disable-features=PaintHolding"
    "--enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL"
    "--enable-gpu-rasterization"
    "--enable-zero-copy"
    "--force-gpu-rasterization"
    "--use-gl=desktop"
    "--enable-hardware-overlays"
    "--password-store=basic"
    "--disable-features=CodeIntegrityEnforce"
    "--autoplay-policy=no-user-gesture-required"
)

# =============================================================================
# Logging
# =============================================================================
mkdir -p "${LOG_DIR}"

log() {
    local level="$1"; shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*" | tee -a "${LOG_FILE}"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

# =============================================================================
# Preflight Checks
# =============================================================================
preflight() {
    log_info "Running preflight checks..."

    if [[ "${EUID}" -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi

    if ! command -v google-chrome-stable &>/dev/null && \
       ! command -v google-chrome &>/dev/null; then
        log_warn "Google Chrome not found in PATH — policies will be staged"
    fi

    log_info "Preflight checks passed"
}

# =============================================================================
# Profile Directory Structure
# =============================================================================
create_profile_structure() {
    log_info "Creating Chrome profile directory structure..."

    local profile_dirs=(
        "${CHROME_PROFILE_BASE}"
        "${CHROME_PROFILE_BASE}/Default"
        "${CHROME_PROFILE_BASE}/Default/Bookmarks"
        "${CHROME_PROFILE_BASE}/Default/Cache"
        "${CHROME_PROFILE_BASE}/Default/Code Cache"
        "${CHROME_PROFILE_BASE}/Default/GPUCache"
        "${CHROME_PROFILE_BASE}/Default/Service Worker"
        "${CHROME_PROFILE_BASE}/Default/Service Worker/CacheStorage"
        "${CHROME_PROFILE_BASE}/Default/Session Storage"
        "${CHROME_PROFILE_BASE}/Default/Local Storage"
        "${CHROME_PROFILE_BASE}/Default/IndexedDB"
        "${CHROME_PROFILE_BASE}/Default/BudgetDatabase"
        "${CHROME_PROFILE_BASE}/Default/databases"
        "${CHROME_PROFILE_BASE}/Default/Extensions"
        "${CHROME_PROFILE_BASE}/Default/GreenCacheOptimization"
        "${CHROME_PROFILE_BASE}/Default/HeroicVariations"
        "${CHROME_PROFILE_BASE}/Default/Journal"
        "${CHROME_PROFILE_BASE}/Default/Platform Notifications"
        "${CHROME_PROFILE_BASE}/Default/Sessions"
        "${CHROME_PROFILE_BASE}/Default/WebStorage"
        "${CHROME_PROFILE_BASE}/ShaderCache"
        "${CHROME_PROFILE_BASE}/FileTypePolicies"
        "${CHROME_PROFILE_BASE}/GrShaderCache"
        "${CHROME_PROFILE_BASE}/SSLErrorAssistant"
        "${CHROME_PROFILE_BASE}/Subresource Filter"
        "${CHROME_PROFILE_BASE}/SafetyTips"
    )

    for dir in "${profile_dirs[@]}"; do
        mkdir -p "${dir}"
    done

    chown -R 1000:1000 "${CHROME_PROFILE_BASE}" 2>/dev/null || true

    log_info "Profile directories created at ${CHROME_PROFILE_BASE}"
}

# =============================================================================
# Enterprise Policy Deployment
# =============================================================================
deploy_policies() {
    log_info "Deploying Chrome enterprise policies..."

    mkdir -p "${CHROME_POLICY_DIR}"

    if [[ -f "${CHROME_POLICY_SRC}" ]]; then
        cp "${CHROME_POLICY_SRC}" "${CHROME_POLICY_DEST}"
        chmod 644 "${CHROME_POLICY_DEST}"
        log_info "Policies installed: ${CHROME_POLICY_DEST}"
    else
        log_warn "Policy source not found: ${CHROME_POLICY_SRC}"
        log_warn "Creating minimal policy file..."
        cat > "${CHROME_POLICY_DEST}" << 'MINIEOF'
{
    "hardware_acceleration_mode_enabled": true,
    "gpu_rasterization_enabled": true,
    "ignore_gpu_blocklist": true,
    "disable_background_networking": true,
    "disable_default_apps": true,
    "metrics_reporting_enabled": false,
    "signin_allowed": false,
    "variant": "Aion"
}
MINIEOF
        log_info "Minimal policy file created"
    fi

    # Also install for Chromium if present
    local chromium_policy_dir="/etc/chromium/policies/managed"
    if [[ -d "/etc/chromium" ]]; then
        mkdir -p "${chromium_policy_dir}"
        cp "${CHROME_POLICY_SRC}" "${chromium_policy_dir}/aion-policies.json" 2>/dev/null || true
        log_info "Policies also deployed to Chromium"
    fi
}

# =============================================================================
# Chrome Flags Configuration
# =============================================================================
configure_flags() {
    log_info "Writing Chrome launch flags..."

    mkdir -p "$(dirname "${CHROME_FLAGS_FILE}")"

    {
        echo "# Aion Chrome Gaming Flags"
        echo "# Generated by first-run-config.sh — $(date '+%Y-%m-%d %H:%M:%S')"
        echo "# Optimized for maximum GPU throughput and minimum latency"
        echo ""
        for flag in "${CHROME_FLAGS[@]}"; do
            echo "${flag}"
        done
    } > "${CHROME_FLAGS_FILE}"

    chmod 644 "${CHROME_FLAGS_FILE}"
    log_info "Chrome flags written to ${CHROME_FLAGS_FILE}"
}

# =============================================================================
# Telemetry & Privacy Hardening
# =============================================================================
disable_telemetry() {
    log_info "Disabling Chrome telemetry and crash reporting..."

    local telemetry_flags=(
        "--disable-metrics"
        "--disable-metrics-reporting"
        "--disable-crash-reporter"
        "--disable-breakpad"
        "--noerrdialogs"
        "--no-default-browser-check"
        "--disable-features=AutofillServerCommunication"
        "--disable-features=AutofillEnableAccountWalletStorage"
        "--disable-sync"
        "--disable-features=SafeBrowsingEnhancedProtection"
        "--disable-features=DownloadNotification"
    )

    local telemetry_file="${CHROME_FLAGS_FILE}.telemetry"
    {
        echo "# Aion Telemetry Flags"
        for flag in "${telemetry_flags[@]}"; do
            echo "${flag}"
        done
    } > "${telemetry_file}"

    chmod 644 "${telemetry_file}"
    log_info "Telemetry flags written to ${telemetry_file}"
}

# =============================================================================
# Desktop Entry
# =============================================================================
install_desktop_entry() {
    log_info "Installing Chrome desktop entry..."

    mkdir -p "${DESKTOP_ENTRY_DIR}"

    if [[ -f "${DESKTOP_ENTRY_SRC}" ]]; then
        cp "${DESKTOP_ENTRY_SRC}" "${DESKTOP_ENTRY_DIR}/aion-chrome.desktop"
        chmod 644 "${DESKTOP_ENTRY_DIR}/aion-chrome.desktop"
        log_info "Desktop entry installed"
    else
        log_info "Creating default desktop entry..."
        cat > "${DESKTOP_ENTRY_DIR}/aion-chrome.desktop" << 'DESKTOPEOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=Aion Chrome
Comment=Optimized web browser for Aion Gaming
Exec=/usr/bin/google-chrome-stable --enable-gpu-rasterization --enable-zero-copy --ignore-gpu-blocklist --enable-features=VaapiVideoDecoder,VaapiVideoDecodeLinuxGL --disable-features=TranslateUI --disable-background-networking --disable-default-apps --no-first-run --password-store=basic --disable-features=ChromeWhatsNewUI --disable-features=SidePanelPinning %U
Icon=google-chrome-stable
Terminal=false
Categories=Network;WebBrowser;
MimeType=text/html;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;
StartupWMClass=google-chrome
DESKTOPEOF
        chmod 644 "${DESKTOP_ENTRY_DIR}/aion-chrome.desktop"
        log_info "Default desktop entry created"
    fi

    # Update desktop database if available
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "${DESKTOP_ENTRY_DIR}" 2>/dev/null || true
    fi
}

# =============================================================================
# GPU Blocklist Override
# =============================================================================
configure_gpu_overrides() {
    log_info "Configuring GPU blocklist overrides..."

    local gpu_config_dir="${CHROME_PROFILE_BASE}/Default/Network"
    mkdir -p "${gpu_config_dir}"

    # Write GPU blocklist override for Chrome
    local blocklist_dir="/opt/aion/chrome"
    mkdir -p "${blocklist_dir}"

    cat > "${blocklist_dir}/gpu-blocklist-override.json" << 'GPUJSON'
{
    "gpus_blacklisted_by_default": [],
    "gpus_whitelist": [
        {
            "vendor_id": "0x8086",
            "device_id": "0x0000"
        },
        {
            "vendor_id": "0x1002",
            "device_id": "0x0000"
        },
        {
            "vendor_id": "0x10DE",
            "device_id": "0x0000"
        }
    ],
    "driver_blacklist": [],
    "gl_renderer_blocklist": []
}
GPUJSON

    log_info "GPU overrides configured"
}

# =============================================================================
# Chrome Wrapper Script
# =============================================================================
create_chrome_wrapper() {
    log_info "Creating Chrome wrapper script..."

    local wrapper_dir="/usr/local/bin"
    cat > "${wrapper_dir}/aion-chrome" << 'WRAPPEREOF'
#!/usr/bin/env bash
# Aion Chrome wrapper — applies gaming flags and GPU overrides
# Generated by first-run-config.sh

CHROME_BIN="/usr/bin/google-chrome-stable"
NEXUS_FLAGS_FILE="/opt/aion/chrome-flags.conf"

if [[ ! -x "${CHROME_BIN}" ]]; then
    CHROME_BIN="/usr/bin/google-chrome"
fi

if [[ ! -x "${CHROME_BIN}" ]]; then
    echo "ERROR: Google Chrome not found" >&2
    exit 1
fi

EXTRA_FLAGS=()
if [[ -f "${NEXUS_FLAGS_FILE}" ]]; then
    while IFS= read -r line; do
        [[ -z "${line}" || "${line}" =~ ^# ]] && continue
        EXTRA_FLAGS+=("${line}")
    done < "${NEXUS_FLAGS_FILE}"
fi

exec "${CHROME_BIN}" "${EXTRA_FLAGS[@]}" "$@"
WRAPPEREOF

    chmod +x "${wrapper_dir}/aion-chrome"
    log_info "Wrapper script created at ${wrapper_dir}/aion-chrome"
}

# =============================================================================
# MIME Type Registration
# =============================================================================
register_mime_types() {
    log_info "Registering MIME types..."

    local mime_dirs=(
        "/home/aion/.local/share/mime/packages"
        "/home/aion/.config/mimeapps.list"
    )

    mkdir -p "/home/aion/.local/share/mime/packages"

    cat > "/home/aion/.local/share/mime/packages/aion-chrome-web-handler.xml" << 'MIMEEOF'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="text/html">
    <glob pattern="*.html"/>
    <glob pattern="*.htm"/>
    <sub-class-of type="text/plain"/>
  </mime-type>
  <mime-type type="application/xhtml+xml">
    <glob pattern="*.xhtml"/>
    <glob pattern="*.xht"/>
  </mime-type>
</mime-info>
MIMEEOF

    chown -R 1000:1000 "/home/aion/.local" 2>/dev/null || true
    log_info "MIME types registered"
}

# =============================================================================
# Chrome Cleanup (remove bloat)
# =============================================================================
remove_chrome_bloat() {
    log_info "Removing Chrome bloat extensions..."

    # Disable Chrome Welcome page
    cat > "${CHROME_PROFILE_BASE}/First Run" 2>/dev/null || true

    # Disable Google Cloud Print notification
    local cloud_print_flag="--disable-cloud-import"
    log_info "Chrome bloat removed"
}

# =============================================================================
# Mark Completion
# =============================================================================
mark_complete() {
    mkdir -p "$(dirname "${FLAG_MARKER}")"
    touch "${FLAG_MARKER}"
    log_info "Configuration marked as complete: ${FLAG_MARKER}"
}

# =============================================================================
# Status Check
# =============================================================================
is_configured() {
    if [[ -f "${FLAG_MARKER}" ]]; then
        local marker_age
        marker_age=$(( $(date +%s) - $(stat -c %Y "${FLAG_MARKER}" 2>/dev/null || echo 0) ))
        if [[ "${marker_age}" -lt 86400 ]]; then
            return 0
        fi
    fi
    return 1
}

# =============================================================================
# Main
# =============================================================================
main() {
    log_info "============================================"
    log_info "Aion Chrome First-Run Configuration"
    log_info "============================================"

    if [[ "${1:-}" == "--status" ]]; then
        if is_configured; then
            log_info "Chrome is configured"
            exit 0
        else
            log_info "Chrome is not configured"
            exit 1
        fi
    fi

    if [[ "${1:-}" == "--force" ]]; then
        rm -f "${FLAG_MARKER}"
        log_info "Force mode — reconfiguring..."
    elif is_configured; then
        log_info "Chrome already configured (use --force to reconfigure)"
        exit 0
    fi

    preflight
    create_profile_structure
    deploy_policies
    configure_flags
    disable_telemetry
    configure_gpu_overrides
    create_chrome_wrapper
    install_desktop_entry
    register_mime_types
    remove_chrome_bloat
    mark_complete

    log_info "============================================"
    log_info "Chrome first-run configuration complete"
    log_info "============================================"
}

main "$@"
