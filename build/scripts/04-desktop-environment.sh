#!/usr/bin/env bash
# Aion Phase 4: Desktop Environment + Gaming Mode
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"

log "=== Aion Phase 4: Desktop + Gaming Mode ==="

arch-chroot /mnt /bin/bash <<'CHROOT'
    # ── KDE Plasma 6 (Wayland) ───────────────────────────────────────
    pacman -S --noconfirm \
        plasma-desktop \
        plasma-workspace \
        plasma-wayland-session \
        sddm \
        sddm-kcm \
        kde-applications-meta \
        konsole \
        dolphin \
        kate \
        spectacle \
        ark \
        kcalc \
        plasma-nm \
        plasma-pa \
        kde-gtk-config \
        breeze-gtk \
        breeze-icons \
        oxygen \
        kscreen \
        powerdevil \
        bluedevil \
        plasma-browser-integration \
        xdg-desktop-portal-kde \
        xdg-desktop-portal-gtk \
        kdeconnect \
        systemsettings \
        discover \
        packagekit-qt6

    # ── Essential apps ───────────────────────────────────────────────
    pacman -S --noconfirm \
        firefox \
        file-roller \
        neovim \
        htop \
        btop \
        fastfetch \
        network-manager-applet \
        brightnessctl \
        xdg-user-dirs \
        xdg-utils \
        noto-fonts \
        noto-fonts-cjk \
        noto-fonts-emoji \
        ttf-liberation \
        ttf-dejavu

    # ── SDDM auto-login to Gaming Mode ──────────────────────────────
    mkdir -p /etc/sddm.conf.d
    cat > /etc/sddm.conf.d/aion.conf <<'SDDM'
[Autologin]
Relogin=true
Session=gamescope-session.desktop

[General]
DisplayServer=wayland

[Wayland]
DisplayDir=/usr/share/wayland-sessions
SDDM

    # ── Gaming Mode session (Steam Big Picture via Gamescope) ────────
    cat > /usr/share/wayland-sessions/gamescope-session.desktop <<'DESKTOP'
[Desktop Entry]
Name=Aion Gaming Mode
Comment=Steam Gaming Mode (Gamescope compositor)
Exec=/usr/bin/aion-gaming-mode
Type=Application
DesktopNames=Aion
DESKTOP

    # ── Gaming mode launcher script ──────────────────────────────────
    cat > /usr/bin/aion-gaming-mode <<'LAUNCHER'
#!/usr/bin/env bash
export SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS=0
export GDK_BACKEND=x11

# Set MangoHud by default for all Vulkan games
export MANGOHUD=1
export MANGOHUD_CONFIGFILE=/etc/MangoHud/MangoHud.conf

# Launch Gamescope wrapping Steam Big Picture
exec gamescope \
    --adaptive-sync \
    --hdr-enabled \
    --rt \
    --force-grab-cursor \
    -w 1920 -h 1080 -r 60 \
    -- \
    steam -gamepadui -steamdeck
LAUNCHER
    chmod +x /usr/bin/aion-gaming-mode

    # ── Desktop Mode session ─────────────────────────────────────────
    cat > /usr/share/wayland-sessions/aion-desktop.desktop <<'DESKTOP'
[Desktop Entry]
Name=Aion Desktop Mode
Comment=Aion KDE Plasma Desktop
Exec=/usr/lib/plasma-systemd-start
Type=Application
DesktopNames=Aion=KDE
DESKTOP

    # ── Toggle script between modes ──────────────────────────────────
    cat > /usr/bin/aion-toggle-mode <<'TOGGLE'
#!/usr/bin/env bash
CURRENT=$(cat /etc/aion-mode 2>/dev/null || echo "gaming")

if [ "$CURRENT" = "gaming" ]; then
    echo "desktop" > /etc/aion-mode
    echo "Switched to Desktop Mode. Restart session to apply."
    sed -i 's|Session=gamescope-session.desktop|Session=aion-desktop.desktop|' /etc/sddm.conf.d/aion.conf
else
    echo "gaming" > /etc/aion-mode
    echo "Switched to Gaming Mode. Restart session to apply."
    sed -i 's|Session=aion-desktop.desktop|Session=gamescope-session.desktop|' /etc/sddm.conf.d/aion.conf
fi
TOGGLE
    chmod +x /usr/bin/aion-toggle-mode
    echo "gaming" > /etc/aion-mode

    # ── Enable SDDM ─────────────────────────────────────────────────
    systemctl enable sddm.service

    # ── Default wallpaper ────────────────────────────────────────────
    mkdir -p /usr/share/wallpapers/Aion
    cp /etc/aion/wallpapers/* /usr/share/wallpapers/Aion/ 2>/dev/null || true

CHROOT

log "=== Phase 4 Complete: Desktop + Gaming Mode ==="
