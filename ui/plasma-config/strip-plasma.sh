#!/bin/bash
# strip-plasma.sh — Strip KDE Plasma 5 to <400 MB RAM
# Usage:  sudo ./strip-plasma.sh [--restore]
set -o pipefail

VERSION="1.0.0"
LOG="/tmp/strip-plasma-$(date +%Y%m%d-%H%M%S).log"
RESTORE=false

for arg in "$@"; do
    case "$arg" in
        --restore) RESTORE=true ;;
        -h|--help)
            printf 'Usage: sudo %s [--restore]\n' "$0"
            exit 0 ;;
        *) printf 'Unknown: %s\n' "$arg" >&2; exit 1 ;;
    esac
done

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }
die()  { printf '[%s] FATAL: %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG" >&2; exit 1; }

[[ $EUID -eq 0 ]]        || die "Run as root: sudo $0"
[[ -n "${SUDO_USER:-}" ]] || die "Use sudo, not direct root"

UHOME="$(eval echo "~${SUDO_USER}")"
UCONF="$UHOME/.config"
USHARE="$UHOME/.local/share"
BACKUP="$UHOME/.plasma-strip-backup"

require() {
    local c; for c in "$@"; do
        command -v "$c" &>/dev/null || die "Missing: $c"
    done
}

# Plasma 6 ships kwriteconfig6; Plasma 5 ships kwriteconfig5.
# Resolve whichever is installed so the script works on both.
KWRITE="$(command -v kwriteconfig6 || command -v kwriteconfig5)"

kcw() {
    su -s /bin/bash "$SUDO_USER" -c \
        "$KWRITE --file '$1' --group '$2' --key '$3' '$4'" 2>/dev/null || true
}

backup() {
    mkdir -p "$BACKUP"
    local f
    for f in plasmashellrc plasma-org.kde.plasma.desktop.appletsrc kwinrc \
             kdeglobals kscreenlockerrc powermanagementprofilesrc kwalletrc \
             baloofilerc nepomukserverrc kded5rc; do
        [[ -f "$UCONF/$f" ]] && cp -a "$UCONF/$f" "$BACKUP/"
    done
    log "Backup → $BACKUP"
}

do_restore() {
    [[ -d "$BACKUP" ]] || die "No backup at $BACKUP"
    local f
    for f in "$BACKUP/"*; do
        [[ -f "$f" ]] && cp -a "$f" "$UCONF/$(basename "$f")"
    done
    kcw "baloofilerc" "Basic Settings" "Indexer Enabled" "true"
    kcw "nepomukserverrc" "Basic Settings" "Start Nepomuk" "true"
    kcw "kwalletrc" "Wallet" "Enabled" "true"
    kcw "kscreenlockerrc" "Daemon" "Autolock" "true"
    kcw "kscreenlockerrc" "Daemon" "Enabled" "true"
    local m
    for m in telemetry baloosearch baloo nepomuk activitymanager; do
        kcw "kded5rc" "Module-${m}" "Enabled" "true"
    done
    rm -f /etc/sudoers.d/ram-cleaner
    su -s /bin/bash "$SUDO_USER" -c "systemctl --user restart plasma-plasmashell.service" 2>/dev/null || true
    log "Restored. Re-login recommended."
}

disable_kded() {
    log "Disabling kded modules..."
    local mods=(telemetry balamatemonitor baloosearch baloo nepomuk
                activitymanager printmanager plasma-browser-integration
                plasma-disks statusnotifierwatcher klipper powerdevil)
    local m
    for m in "${mods[@]}"; do
        kcw "kded5rc" "Module-${m}" "Enabled" "false"
    done
    kcw "kded5rc" "General" "LoadedModules" ""
}

disable_baloo() {
    log "Disabling Baloo..."
    kcw "baloofilerc" "Basic Settings" "Indexer Enabled" "false"
    kcw "baloofilerc" "Basic Settings" "Non-Indexable Paths" \
        "$HOME\\n$HOME/.cache\\n$HOME/.local/share/Trash\\n/tmp\\n/proc\\n/sys\\n/dev"
    su -s /bin/bash "$SUDO_USER" -c "balooctl disable 2>/dev/null; balooctl stop 2>/dev/null" || true
    rm -rf "$UHOME/.local/share/baloo" 2>/dev/null || true
}

disable_nepomuk() {
    log "Disabling Nepomuk..."
    kcw "nepomukserverrc" "Basic Settings" "Start Nepomuk" "false"
    kcw "nepomukserverrc" "Service-nepomukqueryservice" "Enabled" "false"
    kcw "nepomukserverrc" "Service-nepomukstrigiservice" "Enabled" "false"
}

disable_kwallet() {
    log "Disabling KWallet..."
    kcw "kwalletrc" "Wallet" "Enabled" "false"
    kcw "kwalletrc" "Wallet" "Close on idle" "true"
    kcw "kwalletrc" "Wallet" "Close on screensaver" "true"
}

disable_locker() {
    log "Disabling screen locker..."
    kcw "kscreenlockerrc" "Daemon" "Autolock" "false"
    kcw "kscreenlockerrc" "Daemon" "Enabled" "false"
    kcw "kscreenlockerrc" "Lock" "LockOnResume" "false"
    kcw "kscreenlockerrc" "Lock" "LockOnce" "false"
    kcw "kscreenlockerrc" "Greeter" "Enabled" "false"
}

disable_power() {
    log "Disabling power management..."
    local s
    for s in AC Battery LowBattery; do
        kcw "powermanagementprofilesrc" "$s" "SuspendType" "None"
        kcw "powermanagementprofilesrc" "$s" "SuspendTime" "0"
    done
}

disable_animations() {
    log "Disabling animations..."
    kcw "kwinrc" "Compositing" "AnimationSpeed" "0"
    kcw "kwinrc" "Compositing" "Enabled" "true"
    kcw "kwinrc" "Compositing" "Backend" "OpenGL"

    local heavy=(
        kwin4_effect_snow kwin4_effect_flame kwin4_effect_magiclamp
        kwin4_effect_wobblywindows kwin4_effect_cube kwin4_effect_cubeslide
        kwin4_effect_coverswitch kwin4_effect_dashboard kwin4_effect_diminactive
        kwin4_effect_fade kwin4_effect_frozenapp kwin4_effect_glide
        kwin4_effect_morphingpopups kwin4_effect_logout kwin4_effect_Overview
        kwin4_effect_scale kwin4_effect_screenshot kwin4_effect_showfps
        kwin4_effect_showpaint kwin4_effect_slide kwin4_effect_solid
        kwin4_effect_trackmouse kwin4_effect_windowgeometry
        kwin4_effect_blur kwin4_effect_translucency
        kwin4_effect_backgroundcontrast kwin4_effect_shadow kwin4_effect_maximize
    )
    local e
    for e in "${heavy[@]}"; do
        kcw "kwinrc" "Effect-${e}" "Enabled" "false"
    done
    kcw "kwinrc" "Effect-kwin4_effect_outline" "Enabled" "true"
}

set_decoration() {
    log "Setting Breeze + Electric Cyan accent..."
    kcw "kwinrc" "WM" "theme" "Breeze"
    kcw "kdeglobals" "General" "AccentColor" "#00D2FF"
    kcw "kdeglobals" "General" "LastUsedCustomAccentColor" "#00D2FF"
    kcw "kdeglobals" "WM" "activeBackground" "#00D2FF"
    kcw "kdeglobals" "WM" "activeBlend" "#00D2FF"
    kcw "kdeglobals" "WM" "activeForeground" "#FFFFFF"
    kcw "kdeglobals" "WM" "inactiveBackground" "#1E1E1E"
    kcw "kdeglobals" "WM" "inactiveBlend" "#1E1E1E"
    kcw "kdeglobals" "WM" "inactiveForeground" "#999999"
}

install_ram_cleaner() {
    log "Installing RAM cleaner..."

    cat > /usr/local/bin/ram-cleaner << 'BIN'
#!/bin/bash
sync
echo 3 > /proc/sys/vm/drop_caches
BIN
    chmod 755 /usr/local/bin/ram-cleaner

    local eu="${SUDO_USER//\//\\/}"
    cat > /etc/sudoers.d/ram-cleaner << SUDOERS
${eu} ALL=(root) NOPASSWD: /usr/local/bin/ram-cleaner
Defaults:${eu} !syslog
SUDOERS
    chmod 440 /etc/sudoers.d/ram-cleaner
    visudo -cf /etc/sudoers.d/ram-cleaner >/dev/null 2>&1 || die "Invalid sudoers"

    mkdir -p "$USHARE/applications"
    cat > "$USHARE/applications/ram-cleaner.desktop" << DESKTOP
[Desktop Entry]
Name=RAM Cleaner
Comment=Free kernel caches immediately
Exec=sudo /usr/local/bin/ram-cleaner
Icon=utilities-system-monitor
Terminal=false
Type=Application
Categories=System;
X-KDE-Keywords=memory,ram,cache,clean
DESKTOP
    chmod 644 "$USHARE/applications/ram-cleaner.desktop"
    su -s /bin/bash "$SUDO_USER" -c "update-desktop-database '$USHARE/applications'" 2>/dev/null || true
}

configure_panel() {
    log "Configuring bottom panel..."

    local pfile="$UCONF/plasma-org.kde.plasma.desktop.appletsrc"
    cat > "$pfile" << 'PANEL'
[Configuration]
Update=true

[Containments][2]
activityId=
formfactor=2
immutability=0
location=bottom
plugin=org.kde.plasma.panel

[Containments][2][Applets][100]
immutability=0
plugin=org.kde.plasma.kickoff

[Containments][2][Applets][100][Configuration][General]
showRecentApps=false
showRecentDocs=false
showRecentContacts=false
showPowerOptions=true
systemApplications=applications:org.kde.konsole.desktop,applications:systemsettings.desktop

[Containments][2][Applets][101]
immutability=0
plugin=org.kde.panelstretchspacer

[Containments][2][Applets][102]
immutability=0
plugin=org.kde.plasma.icontasks

[Containments][2][Applets][102][Configuration][General]
groupBy=false
sorting=0
showToolTips=true

[Containments][2][Applets][103]
immutability=0
plugin=org.kde.plasma.systemtray

[Containments][2][Applets][103][Configuration]
SystrayContainmentId=200

[Containments][2][Applets][104]
immutability=0
plugin=org.kde.plasma.digitalclock

[Containments][2][Applets][104][Configuration][Appearance]
showDate=true
showSeconds=false
showTimezone=false
use24hFormat=true
dateFormat=shortDate

[Containments][2][Applets][105]
immutability=0
plugin=org.kde.panelstretchspacer

[Containments][2][Applets][106]
immutability=0
plugin=org.kde.plasma.icontasks

[Containments][2][Applets][106][Configuration][General]
launchers=applications:ram-cleaner.desktop
groupBy=false
sorting=0
showToolTips=true
isInLauncher=true

[Containments][200]
activityId=
formfactor=2
immutability=0
location=bottom
plugin=org.kde.plasma.systemtray
PANEL

    chown "$SUDO_USER":"$SUDO_USER" "$pfile"
    chmod 644 "$pfile"
    log "Panel: [launcher] — [tasks] — [systray] — [clock] — [ram-cleaner]"
}

disable_services() {
    log "Disabling services and killing bloat..."
    local svcs=(baloo-fileindexer.service nepomukserver.service
                plasma-browser-integration.service klipper.service)
    local s
    for s in "${svcs[@]}"; do
        su -s /bin/bash "$SUDO_USER" -c \
            "systemctl --user disable --now '$s'" 2>/dev/null || true
    done
    su -s /bin/bash "$SUDO_USER" -c \
        "killall -q baloo_file nepomukserver strigiclient klipper 2>/dev/null" 2>/dev/null || true
}

thin_panel() {
    kcw "plasmashellrc" "PlasmaViews" "panelThickness" "40"
    kcw "plasmashellrc" "PlasmaViews" "panelVisibility" "0"
    kcw "plasmashellrc" "PlasmaViews" "panelMinimumHeight" "40"
}

minimal_desktop() {
    kcw "plasmashellrc" "PlasmaViews" "Desktops" "Number" "1"
    kcw "plasmashellrc" "Wallpaper" "default" "plugin" "org.kde.plasma.color"
    kcw "plasmashellrc" "Wallpaper" "default" "Color" "#0E0E12"
}

reload_plasma() {
    log "Reloading Plasma..."
    su -s /bin/bash "$SUDO_USER" -c \
        "systemctl --user restart plasma-plasmashell.service" 2>/dev/null || \
    su -s /bin/bash "$SUDO_USER" -c \
        "qdbus org.kde.plasmashell /PlasmaShell reinitialize" 2>/dev/null || true
    su -s /bin/bash "$SUDO_USER" -c \
        "qdbus org.kde.KWin /KWin reconfigure" 2>/dev/null || true
    sleep 1
    su -s /bin/bash "$SUDO_USER" -c \
        "systemctl --user restart kwin_x11.service 2>/dev/null || \
         systemctl --user restart kwin_wayland.service 2>/dev/null || true" 2>/dev/null || true
}

print_summary() {
    log ""
    log "=========================================="
    log " STRIP-PLASMA v${VERSION} — COMPLETE"
    log "=========================================="
    log " Memory:  free -h"
    log " Backup:  $BACKUP"
    log " Restore: sudo $0 --restore"
    log " Log:     $LOG"
    log ""
    log " Disabled:"
    log "   Telemetry, Baloo, Nepomuk, KWallet,"
    log "   screen locker, power management, animations"
    log ""
    log " Panel: [launcher] [tasks] [systray] [clock] [RAM]"
    log " RAM Cleaner: click icon → sync + drop_caches (no pw)"
    log " Recommended: re-login for full effect."
    log "=========================================="
}

# ===========================================================================
if $RESTORE; then
    [[ -n "$KWRITE" ]] || die "Missing: kwriteconfig6/kwriteconfig5"
    UHOME="$(eval echo "~${SUDO_USER}")"
    UCONF="$UHOME/.config"
    USHARE="$UHOME/.local/share"
    BACKUP="$UHOME/.plasma-strip-backup"
    do_restore
    exit 0
fi

[[ -n "$KWRITE" ]] || die "Missing: kwriteconfig6/kwriteconfig5"
log "=== strip-plasma.sh v${VERSION} ==="
log "User: $SUDO_USER ($UHOME)"

backup
disable_kded
disable_baloo
disable_nepomuk
disable_kwallet
disable_locker
disable_power
disable_animations
set_decoration
install_ram_cleaner
configure_panel
thin_panel
minimal_desktop
disable_services
reload_plasma
print_summary
