#!/usr/bin/env bash
# Aion Phase 3: Gaming Stack Installation
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"

log "=== Aion Phase 3: Gaming Stack ==="

arch-chroot /mnt /bin/bash <<'CHROOT'
    # ── Flatpak ──────────────────────────────────────────────────────
    pacman -S --noconfirm flatpak

    flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

    # ── Core gaming packages (pacman) ────────────────────────────────
    pacman -S --noconfirm \
        steam \
        steam-native-runtime \
        proton-ge-custom-bin \
        gamescope \
        mangohud \
        gamemode \
        lib32-gamemode \
        vkBasalt \
        lib32-vkBasalt \
        wine \
        wine-gecko \
        wine-mono \
        lib32-vulkan-icd-loader \
        lib32-vulkan-validation-layers \
        vulkan-icd-loader \
        vulkan-validation-layers \
        vulkan-tools \
        mesa \
        lib32-mesa \
        lib32-mesa-vdpau \
        libva-mesa-driver \
        mesa-vdpau \
        lib32-libva-mesa-driver \
        nvidia-utils \
        lib32-nvidia-utils \
        opencl-nvidia \
        lib32-opencl-nvidia \
        xf86-video-amdgpu \
        vulkan-radeon \
        lib32-vulkan-radeon \
        lib32-mesa \
        xdg-desktop-portal \
        xdg-desktop-portal-gtk \
        xdg-desktop-portal-kde \
        pipewire \
        pipewire-pulse \
        pipewire-jack \
        wireplumber \
        pavucontrol \
        alsa-utils \
        alsa-plugins \
        lib32-alsa-plugins \
        lib32-libpulse \
        lib32-sdl2 \
        lib32-sdl2_image \
        lib32-gtk3 \
        giflib \
        lib32-giflib \
        libpng \
        lib32-libpng \
        libjpeg-turbo \
        lib32-libjpeg-turbo \
        lib32-freetype2 \
        lib32-fontconfig \
        lib32-libxrandr \
        lib32-libxinerama \
        lib32-libxi \
        lib32-libxcursor \
        lib32-libxcomposite \
        lib32-libxss \
        lib32-alsa-lib \
        lib32-mpg123 \
        lib32-openssh \
        p7zip \
        unrar \
        unzip \
        zip \
        ntfs-3g \
        dosfstools \
        exfatprogs

    # ── Heroic Games Launcher (AUR helper needed) ────────────────────
    pacman -S --noconfirm yay

    # ── Flatpak gaming apps ──────────────────────────────────────────
    flatpak install -y flathub com.heroicgameslauncher.HeroicGamesLauncher
    flatpak install -y flathub com.usebottles.Bottles
    flatpak install -y flathub net.retroarch.RetroArch
    flatpak install -y flathub org.DolphinEmu.dolphin-emu
    flatpak install -y flathub org.cemu_emu.Cemu
    flatpak install -y flathub org.yuzu_emu.yuzu
    flatpak install -y flathub com.mojang.Minecraft

    # ── Performance tuning ───────────────────────────────────────────
    # Enable GameMode
    systemctl enable gamemoded.service

    # Enable PipeWire audio (lowest latency for gaming)
    systemctl enable pipewire.service
    systemctl enable pipewire-pulse.service
    systemctl enable wireplumber.service

    # Pre-compile shader cache
    systemctl enable steam-cache.service 2>/dev/null || true

    # ── Kernel parameters for gaming ─────────────────────────────────
    # Add gaming-optimized kernel parameters to boot entry
    sed -i 's/rw mitigations=off/rw mitigations=off \
        nowatchdog \
        tsc=reliable \
        clocksource=tsc tsc=unstable \
        nvidia-drm.modeset=1 \
        nvidia-drm.fbdev=1 \
        rd.udev.log_priority=3 \
        vt.global_cursor_default=0 \
        loglevel=3 \
        splash \
        systemd.unified_cgroup_hierarchy=1/' \
        /boot/loader/entries/aion.conf

    # ── GameScope session for Steam Gaming Mode ──────────────────────
    mkdir -p /etc/systemd/system/gamescope-session.service.d
    cat > /etc/systemd/system/gamescope-session.service.d/aion.conf <<'UNIT'
[Service]
Environment="GAMESCOPE_WIDTH=1920"
Environment="GAMESCOPE_HEIGHT=1080"
Environment="GAMESCOPE_REFRESH_RATE=60"
Environment="GAMESCOPE_MODE=fullscreen"
UNIT

CHROOT

# ── Gaming configs ──────────────────────────────────────────────────
log "Installing gaming configuration files..."
cp "$SCRIPT_DIR/../../configs/gaming/mangohud.conf" /mnt/etc/MangoHud/MangoHud.conf 2>/dev/null || true
cp "$SCRIPT_DIR/../../configs/gaming/vkbasalt.conf" /mnt/etc/vkBasalt/vkBasalt.conf 2>/dev/null || true

log "=== Phase 3 Complete: Gaming stack installed ==="
