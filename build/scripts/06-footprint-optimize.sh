#!/usr/bin/env bash
# Aion Phase 6: Micro-Optimized Footprint
# Final hardening + size reduction pass (runs inside arch-chroot).
# - Compiler/package flags for smaller binaries
# - Aggressive but safe cache/artifact cleanup
# - Journald size caps
# - Byte-compilation cache removal (Python)
# - Unneeded service masking
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Aion]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Must run as root"

log "=== Aion Phase 6: Footprint Optimization ==="

arch-chroot /mnt /bin/bash <<'CHROOT'
    set -euo pipefail

    # ── 1. Compiler / packaging flags ────────────────────────────────
    # Parallel builds + lower binaries (applies to future source builds).
    mkdir -p /etc/makepkg.conf.d
    cat > /etc/makepkg.conf.d/aion-optimize.conf <<'MKCONF'
MAKEFLAGS="-j$(nproc)"
# Aggressive size optimizations for packages we build from source
OPTIONS+=(!docs !manpages)
CARCH_CFLAGS+=" -O2 -pipe"
MKCONF
    chmod 644 /etc/makepkg.conf.d/aion-optimize.conf

    # Python: never write .pyc caches on a read-only root.
    mkdir -p /etc/python
    cat > /etc/python/pyvenv.cfg <<'PYCFG'
home = /usr/bin
include-system-site-packages = true
version = 3
executable = /usr/bin/python3
PYCFG

    cat > /etc/environment.d/aion.conf <<'ENVD'
PYTHONDONTWRITEBYTECODE=1
PYTHONOPTIMIZE=1
ENVD
    chmod 644 /etc/environment.d/aion.conf

    # ── 2. Journald size caps (bounded logs) ─────────────────────────
    mkdir -p /etc/systemd/journald.conf.d
    cat > /etc/systemd/journald.conf.d/aion-size.conf <<'JRNL'
[Journal]
SystemMaxUse=100M
SystemMaxFileSize=20M
RuntimeMaxUse=50M
MaxRetentionSec=14d
Compress=yes
JRNL
    chmod 644 /etc/systemd/journald.conf.d/aion-size.conf

    # ── 3. Safe cache/artifact cleanup ───────────────────────────────
    log "Cleaning pacman cache (keep newest 1)..."
    paccache -rk1 -ruk0 2>/dev/null || pacman -Scc --noconfirm

    log "Purging stale bytecode and build artifacts..."
    find /usr/lib/python3* /usr/local/lib/python3* -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find /usr/lib/python3* /usr/local/lib/python3* -type f -name '*.pyc' -delete 2>/dev/null || true
    rm -rf /var/cache/pacman/pkg/* 2>/dev/null || true
    rm -rf /tmp/* /var/tmp/* 2>/dev/null || true
    rm -rf /root/.cache/* 2>/dev/null || true

    # ── 4. Unneeded services (masked, not removed) ───────────────────
    log "Masking non-essential services..."
    for svc in \
        avahi-daemon.service \
        cups.service \
        ModemManager.service \
        pcscd.service \
        packagekit.service \
        vmtoolsd.service \
        vmware-vmblock-fuse.service \
        hv_fcopy_daemon.service \
        hv_kvp_daemon.service \
        hv_vss_daemon.service; do
        systemctl mask "$svc" 2>/dev/null || true
    done

    # ── 5. Btrfs default compression ─────────────────────────────────
    log "Setting default Btrfs compression to zstd..."
    btrfs filesystem defrag -rczstd /usr 2>/dev/null || true

    # ── 6. Initramfs with size-conscious compression ─────────────────
    log "Rebuilding initramfs with zstd compression..."
    if grep -q "COMPRESSION" /etc/mkinitcpio.conf 2>/dev/null; then
        sed -i 's|^#\?COMPRESSION=.*|COMPRESSION="zstd"|' /etc/mkinitcpio.conf
    else
        echo 'COMPRESSION="zstd"' >> /etc/mkinitcpio.conf
    fi
    mkinitcpio -P

    log "Footprint optimization complete."
CHROOT

log "=== Phase 6 Complete: System footprint optimized ==="
