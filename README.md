# Aion

**Arch-based immutable Gaming OS — replaces Windows for gamers.**

## What is Aion?

A complete gaming operating system built on Arch Linux:
- **Immutable Btrfs root** — Atomic updates with A/B snapshots, read-only system
- **A/B Boot** — BLS boot entries with auto-rollback after 3 failed boots
- **Gaming kernel** — CachyOS BORE scheduler, AutoFDO, PGO optimizations
- **Pre-compiled NVIDIA** — No dkms, matched to kernel, DRM modeset enabled
- **Full gaming stack** — Steam, Proton/GE-Proton, Heroic, Lutris, RetroArch
- **Unified store** — one hub for Steam, Lutris, Heroic and Bottles libraries
- **Zero-setup emulation** — one-command RetroArch PSX bridge (`aion-emu`)
- **Neon boot splash** — animated Plymouth infinity-loop theme
- **Steam Gaming Mode** — Console-like UI via Gamescope compositor
- **Android apps** — Waydroid with SELinux sandbox isolation
- **Quick Resume** — CRIU-based game freeze/restore (checkpoint to SSD)
- **Dynamic VRAM** — Auto-scaling GTT allocation for AMD APUs
- **Cloud Sync** — Rclone-based save sync (Google Drive, OneDrive, Nextcloud)
- **Zero-Lag Recording** — NVENC/VA-API + PipeWire capture
- **Flatpak theme override** — Consistent theming across all Flatpak apps
- **Hardware auto-detection** — GPU, CPU scheduler, storage, controllers
- **Zero telemetry** — No data collection, full privacy
- **One command update** — `sudo aion-update` for atomic system updates

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Aion                            │
├─────────────────────────────────────────────────────┤
│  Gaming Mode (Gamescope + Steam Big Picture)         │
│  ─── or ───                                          │
│  Desktop Mode (KDE Plasma 6 Wayland)                 │
├─────────────────────────────────────────────────────┤
│  Gaming Stack                                         │
│  Steam · Proton · Heroic · Lutris · RetroArch        │
│  MangoHud · GameScope · vkBasalt · GameMode          │
├─────────────────────────────────────────────────────┤
│  Core Services                                        │
│  Hardware Adapter · Input Engine · Security Daemon   │
│  Audio Routing · Storage Optimizer · E-GPU Manager   │
├─────────────────────────────────────────────────────┤
│  Android Layer                                        │
│  Waydroid · APK Installer · Key Mapper               │
├─────────────────────────────────────────────────────┤
│  Gaming Kernel (CachyOS BORE + AutoFDO)              │
├─────────────────────────────────────────────────────┤
│  Btrfs A/B Immutable Root                             │
│  Read-only system + atomic snapshots                  │
└─────────────────────────────────────────────────────┘
```

## Build

### Prerequisites
- Arch Linux (or Fedora/Ubuntu for cross-compilation)
- Root privileges
- ~10 GB free disk space

### ISO Only
```bash
sudo ./Aion-Builder.sh 1.0.0
```

### Direct Install
```bash
sudo ./Aion-Builder.sh 1.0.0 --install /dev/nvme0n1
```

### Modular Build (each phase independently)
```bash
sudo ./build/scripts/01-base-system.sh
sudo ./build/scripts/02-gaming-kernel.sh
sudo ./build/scripts/03-gaming-stack.sh
sudo ./build/scripts/04-desktop-environment.sh
sudo ./build/scripts/05-update-system.sh
```

## System Commands

| Command | Description |
|---------|-------------|
| `aion-mode gaming` | Switch to Gaming Mode |
| `aion-mode desktop` | Switch to Desktop Mode |
| `sudo aion-update` | Atomic system update with snapshot |
| `sudo aion-rollback` | Rollback to previous snapshot |
| `sudo aion-hardware` | Auto-detect and install drivers |
| `sudo aion-debloat` | Remove telemetry and bloat |

## Testing

```bash
# Full test suite (388 tests)
python -m pytest tests/ -v

# Validation tests only (240 tests)
python -m pytest tests/validation/ -v

# Main test suite (108 tests)
python -m pytest tests/test-aion.py -v

# Regression tests (Linux only, 36 skipped on Windows)
python -m pytest tests/regression/ -v

# Standalone math validation
python tests/test-all-math.py
```

## Project Structure

```
Aion/
├── Aion-Builder.sh           # Master build script (2073 lines)
├── boot/
│   └── plymouth/aion-neon/       # Neon-blue infinity loop boot splash
├── build/
│   ├── build.sh                 # Modular build entry point
│   └── scripts/
│       ├── 01-base-system.sh        # Btrfs immutable root + systemd-boot
│       ├── 02-gaming-kernel.sh      # CachyOS BORE kernel
│       ├── 03-gaming-stack.sh       # Gaming packages + Flatpak apps
│       ├── 04-desktop-environment.sh # KDE + Gaming Mode sessions
│       ├── 05-update-system.sh      # Atomic updater + rollback tools
│       └── 06-footprint-optimize.sh # Shrink image + remove cruft
├── config/
│   └── aion-config.json      # Master system config
├── core/
│   ├── audio/                   # PipeWire audio routing
│   ├── hardware-adapter/        # GPU/CPU/storage auto-detection
│   ├── input-engine/            # Controller + keyboard input
│   ├── network/                 # Port forwarding
│   ├── security/                # SELinux policy + security daemon
│   └── services/                # Immutable root init + systemd units
├── android/
│   ├── waydroid/                # Waydroid container setup
│   ├── apk-installer/           # APK handler for Android apps
│   └── key-mapper/              # Gamepad-to-keyboard mapping
├── chrome/                      # Browser integration + policies
├── ui/
│   ├── oobe/                    # First-run wizard (PyQt6)
│   ├── live-wallpaper/          # Animated wallpaper engine
│   ├── theme-switcher/          # Theme management
│   ├── game-capture/            # Screenshot/recording daemon
│   ├── plasma-config/           # KDE Plasma strip-down
│   ├── resolution/              # Multi-monitor resolution
│   └── icons/                   # Icon manager
├── games/
│   ├── tweak-hub/               # GPU monitor + performance tweaking
│   ├── wine-installer/          # Wine game installer + .exe handler
│   └── emulation/               # Zero-setup RetroArch PSX bridge (aion-emu)
├── hub/
│   ├── aion-hub-server.py       # Local-first app/game portal
│   ├── library-bridge.py        # Unified store (Steam/Lutris/Heroic)
│   └── web/                     # Hub web assets
├── performance/
│   ├── compression/             # Btrfs zstd:3 optimization
│   ├── throttler/               # Resource throttling daemon
│   └── zram/                    # ZRAM virtual memory
├── deploy/
│   ├── github/                  # CI/CD + ISO build + Pages
│   └── ota/                     # OTA update system
├── installer/
│   └── oobe/                    # Graphical installer wizard
├── calamares/                   # Calamares installer config
├── configs/gaming/              # MangoHud + vkBasalt configs
├── tests/
│   ├── validation/              # Feature + math validation (240 tests)
│   ├── regression/              # Regression tests (Linux, 4 files)
│   ├── security/                # Security tests (3 files)
│   ├── test-aion.py          # Main test suite (108 tests)
│   └── test-all-math.py         # Math validation runner
├── features/                      # Killer features
│   ├── quick-resume.py           # CRIU game freeze/restore
│   ├── vram-scaler.py            # Dynamic GTT/VRAM scaling
│   ├── cloud-sync.py             # Rclone-based save sync
│   └── zero-lag-record.py        # Hardware encoder recording
├── docs/                        # Project website
└── .github/workflows/           # CI/CD pipelines
```

## Key Features

### Immutable Root (Btrfs A/B)
- System root is read-only by default
- `/etc` has writable overlay for user configuration
- `/var` is tmpfs-backed for runtime data
- Every update creates a snapshot — automatic rollback on failure

### Gaming Kernel (CachyOS BORE)
- Burst-Oriented Response Enhancer scheduler
- AutoFDO + PGO optimized for gaming workloads
- 1000Hz timer frequency, PREEMPT_DYNAMIC
- AMD P-State, Intel Thread Director support
- NVIDIA DRM modeset enabled

### Gaming Stack
- **Steam** + Proton + GE-Proton (out-of-box)
- **Heroic Games Launcher** (Epic/GOG/Amazon)
- **Lutris** (multi-platform game manager)
- **RetroArch** + PSX core (zero-setup emulation via `aion-emu`)
- **MangoHud** (performance overlay, enabled by default)
- **GameScope** (micro-compositor for tear-free gaming)
- **vkBasalt** (CAS sharpening enabled)
- **GameMode** (auto CPU/GPU optimization)

### Unified Store (Aion Hub)
One portal for everything you own, regardless of launcher:
- Local-first web portal at `http://127.0.0.1:8931` (stdlib-only, no cloud)
- Aggregates installed games from **Steam, Lutris, Heroic, Bottles, Aion-native**
  and Flatpak apps into a single library (`hub/library-bridge.py`)
- `GET /api/library` — unified installed-games list
- `GET /api/library/stats` — per-source counts + total size
- One-click install queue for catalog apps with per-client rate limiting

### Zero-Setup Emulation (`aion-emu`)
Drop PlayStation ROMs into `~/ROMs`, then:
```bash
aion-emu status              # Show RetroArch / core / BIOS state
aion-emu setup               # Install RetroArch + PSX core + config
aion-emu list                # List playable ROMs
aion-emu launch "Spyro"      # Launch a ROM
```
- Auto-downloads the `pcsx_rearmed` core from RetroArch's buildbot
- Auto-detects a PlayStation BIOS in `~/.config/retroarch/system`
- Writes a neon-friendly default config only when none exists
- Every step degrades gracefully: no network, no core, no BIOS — never hangs

### Boot Splash
- **Aion Neon** Plymouth theme (`boot/plymouth/aion-neon`)
- Animated neon-blue infinity loop with comet trail + boot progress bar
- Installed by default (mkinitcpio `plymouth` hook + `plymouthd.conf`)

### Android Integration (Waydroid)
- Run Android apps natively on Linux
- APK installer with automatic configuration
- Gamepad-to-keyboard mapping with profiles
- Shared file system between host and container

### Hardware Auto-Detection
- GPU detection and driver installation (NVIDIA/AMD/Intel)
- E-GPU hotplug support
- Storage optimization for SSD/NVMe
- CPU scheduler auto-tuning
- Controller input mapping

### Desktop Mode
- KDE Plasma 6 on Wayland (stripped for performance)
- Full desktop with Dolphin, Konsole, Firefox
- Live wallpaper engine
- Theme switcher with gaming themes
- Game capture (screenshot/recording)

### Security
- SELinux enforcing policy
- Security bypass daemon with audit logging
- Service hardening (sandbox, capabilities, restart limits)
- No hardcoded secrets in source code
- Wine prefix isolation

## Killer Features

### Quick Resume (CRIU)
Freeze any game to an SSD checkpoint and restore it later — like PS5's Activity Cards.
```bash
aion-quick-resume freeze --game "Cyberpunk 2077"   # Freeze game
aion-quick-resume restore --game "Cyberpunk 2077"  # Restore game
aion-quick-resume list                              # List saved states
aion-quick-resume daemon                            # Auto-save daemon
```

### Dynamic VRAM Scaler (AMD APUs)
Automatically adjusts GTT (Graphics) memory allocation based on system RAM:
- 4GB RAM → 512MB GTT (Android apps)
- 8GB RAM → 1024MB GTT (indie games)
- 16GB RAM → 2048MB GTT (AAA gaming)
- 32GB RAM → 4096MB GTT (4K gaming)

Monitors sysfs and scales up/down based on memory pressure thresholds.

### Cloud Sync (Rclone)
Sync non-Steam game saves to cloud storage. Supports Google Drive, OneDrive, Nextcloud.
```bash
aion-cloud-sync setup    # Configure cloud provider
aion-cloud-sync sync     # Sync all saves
aion-cloud-sync restore  # Restore from cloud
aion-cloud-sync list     # List syncable games
```

Syncs: Wine prefixes, Lutris saves, RetroArch saves/states, Steam Proton, Cemu, Dolphin, Yuzu.

### Zero-Lag Recording
Record gameplay with zero FPS impact using hardware encoders:
- **NVIDIA**: NVENC (H.264/HEVC)
- **AMD**: VA-API (H.264/HEVC)
- **Intel**: QSV (H.264/HEVC)
- **Fallback**: PipeWire + software encoding

```bash
aion-zero-lag-record --game "Cyberpunk 2077"   # Record game
aion-zero-lag-record --screen                   # Record screen
aion-zero-lag-record --replay                   # Save instant replay
aion-zero-lag-record --config                   # Show encoder info
```

Presets: `quality` (20Mbps HEVC), `balanced` (15Mbps), `performance` (10Mbps H.264).

## License

See [LICENSE](LICENSE) for details.
