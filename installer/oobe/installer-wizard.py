#!/usr/bin/env python3
import sys
import os
import json
import time
import shutil
import signal
import locale
import logging
import subprocess
import tempfile
from pathlib import Path
from functools import partial

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QScrollArea, QFrame, QStackedWidget,
    QProgressBar, QTextEdit, QSlider, QFileDialog, QMessageBox,
    QGridLayout, QSizePolicy, QGraphicsDropShadowEffect, QDesktopWidget,
    QLineEdit, QSpacerItem
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer, QSize, QPoint,
    QRect, QThread, pyqtSignal, QParallelAnimationGroup, QSequentialAnimationGroup,
    pyqtSlot, QObject
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPainterPath, QLinearGradient,
    QBrush, QPen, QFontDatabase, QIcon, QPixmap, QPalette,
    QScreen, QFontMetrics
)

LOCK_FILE = "/tmp/nexusos-installer.lock"
LOG_FILE = "/var/log/nexusos/installer.log"
INSTALL_MEDIA_PATH = "/run/live/media/nexusos"
NEXUSOS_MOUNT = "/mnt/nexusos"
GRUB_MARKER = "NexusOS"

BG_PRIMARY = "#121212"
BG_SECONDARY = "#1A2238"
BG_CARD = "#1E2A3A"
BG_CARD_HOVER = "#243447"
ACCENT = "#00D2FF"
ACCENT_DIM = "#0099BB"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#8899AA"
TEXT_DIM = "#556677"
DANGER = "#FF4444"
WARNING = "#FFB344"
SUCCESS = "#44FF88"

TRANSITION_MS = 350
SCREEN_W = 0
SCREEN_H = 0


def _log(msg, level="INFO"):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}\n")
    except Exception:
        pass


def _parse_size_to_gb(size_str):
    if not size_str:
        return 0.0
    size_str = size_str.strip().upper()
    multipliers = {
        "T": 1024.0,
        "G": 1.0,
        "M": 1.0 / 1024.0,
        "K": 1.0 / (1024.0 * 1024.0),
        "B": 1.0 / (1024.0 * 1024.0 * 1024.0),
    }
    for suffix, mult in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return float(size_str[:-1]) * mult
            except ValueError:
                return 0.0
    try:
        return float(size_str)
    except ValueError:
        return 0.0


def detect_disks():
    try:
        result = subprocess.run(
            ["lsblk", "-d", "-o", "NAME,SIZE,TYPE,FSTYPE,MODEL", "-J"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            _log(f"lsblk failed: {result.stderr}", "ERROR")
            return []
        data = json.loads(result.stdout)
        disks = []
        for dev in data.get("blockdevices", []):
            if dev.get("type") == "disk":
                disks.append({
                    "name": dev["name"],
                    "size_gb": _parse_size_to_gb(dev.get("size", "0")),
                    "type": dev.get("type", ""),
                    "fstype": dev.get("fstype", "") or "unknown",
                    "model": (dev.get("model") or "Unknown").strip(),
                })
        _log(f"Detected {len(disks)} disks")
        return disks
    except Exception as e:
        _log(f"Disk detection failed: {e}", "ERROR")
        return []


def detect_partitions(device):
    try:
        result = subprocess.run(
            ["parted", "-m", f"/dev/{device}", "unit", "GB", "print"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        partitions = []
        for line in result.stdout.strip().split("\n"):
            if ":" not in line:
                continue
            parts = line.split(":")
            if len(parts) < 6:
                continue
            try:
                pnum = parts[0].strip()
                start = parts[1].strip()
                end = parts[2].strip()
                size = _parse_size_to_gb(parts[3])
                fstype = parts[4].strip().lower()
                name = parts[5].strip()
                if fstype and fstype != "":
                    partitions.append({
                        "number": pnum,
                        "start": start,
                        "end": end,
                        "size_gb": size,
                        "filesystem": fstype,
                        "name": name,
                    })
            except (IndexError, ValueError):
                continue
        return partitions
    except Exception as e:
        _log(f"Partition detection failed for {device}: {e}", "ERROR")
        return []


def detect_windows_partition(device):
    parts = detect_partitions(device)
    for p in parts:
        if p["filesystem"] in ("ntfs", "fat32"):
            return p
    return None


def detect_free_space(device):
    try:
        result = subprocess.run(
            ["parted", "-m", f"/dev/{device}", "unit", "GB", "print", "free"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return 0.0
        max_free = 0.0
        for line in result.stdout.strip().split("\n"):
            if "free" in line.lower() or ("free" in line.lower()):
                parts = line.split(":")
                if len(parts) >= 4:
                    try:
                        size = _parse_size_to_gb(parts[3])
                        if size > max_free:
                            max_free = size
                    except (IndexError, ValueError):
                        pass
        if max_free == 0.0:
            total = sum(d["size_gb"] for d in detect_disks() if d["name"] == device)
            used = sum(p["size_gb"] for p in detect_partitions(device))
            if total > 0:
                max_free = total - used
        return max_free
    except Exception as e:
        _log(f"Free space detection failed: {e}", "ERROR")
        return 0.0


def get_last_partition(device):
    parts = detect_partitions(device)
    if not parts:
        return 0
    return max(int(p["number"]) for p in parts)


def is_device_mounted(device):
    try:
        result = subprocess.run(
            ["findmnt", "-rno", "SOURCE", f"/dev/{device}"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() != ""
    except Exception:
        return False


def is_device_removable(device):
    try:
        path = f"/sys/block/{device}/removable"
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip() == "1"
        return False
    except Exception:
        return False


def deploy_nexusos_files(target, steps_callback=None):
    if steps_callback:
        steps_callback(45, "Copying system files...")
    src = INSTALL_MEDIA_PATH
    if not os.path.isdir(src):
        src = "/cdrom"
    if not os.path.isdir(src):
        raise RuntimeError(f"Cannot find install media at {INSTALL_MEDIA_PATH} or /cdrom")
    dest = f"{NEXUSOS_MOUNT}"
    os.makedirs(dest, exist_ok=True)
    try:
        subprocess.run(
            ["mount", target, dest],
            check=True, capture_output=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to mount {target}: {e}")
    rsync_exclude = [
        "--exclude=/dev/*", "--exclude=/proc/*", "--exclude=/sys/*",
        "--exclude=/tmp/*", "--exclude=/run/*", "--exclude=/mnt/*",
        "--exclude=/lost+found", "--exclude=/boot/efi",
    ]
    try:
        subprocess.run(
            ["rsync", "-aAXv"] + rsync_exclude + [f"{src}/", f"{dest}/"],
            check=True, capture_output=True, timeout=600
        )
    except subprocess.CalledProcessError as e:
        subprocess.run(["umount", dest], capture_output=True)
        raise RuntimeError(f"Rsync failed: {e}")
    subprocess.run(["umount", dest], capture_output=True)
    _log(f"Deployed NexusOS files to {target}")


def create_btrfs_subvolumes(device):
    mnt = "/tmp/nexusos-btrfs-setup"
    os.makedirs(mnt, exist_ok=True)
    try:
        subprocess.run(
            ["mount", device, mnt], check=True, capture_output=True, timeout=30
        )
        for subvol in ["@", "@home", "@var-log", "@tmp", "@snapshots"]:
            subprocess.run(
                ["btrfs", "subvolume", "create", f"{mnt}/{subvol}"],
                check=True, capture_output=True, timeout=30
            )
        subprocess.run(["umount", mnt], capture_output=True)
    except subprocess.CalledProcessError as e:
        subprocess.run(["umount", mnt], capture_output=True)
        raise RuntimeError(f"Btrfs subvolume creation failed: {e}")
    _log("Created Btrfs subvolumes: @, @home, @var-log, @tmp, @snapshots")


def configure_grub_dualboot(device):
    grub_cfg = f"""# NexusOS GRUB Configuration (Dual-Boot)
GRUB_DEFAULT=saved
GRUB_TIMEOUT=5
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR="NexusOS"
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""
GRUB_DISABLE_OS_PROBER=false
"""
    _write_grub_config(grub_cfg)


def configure_grub_standalone(device):
    grub_cfg = f"""# NexusOS GRUB Configuration (Standalone)
GRUB_DEFAULT=0
GRUB_TIMEOUT=3
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR="NexusOS"
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""
GRUB_DISABLE_OS_PROBER=true
"""
    _write_grub_config(grub_cfg)


def _write_grub_config(content):
    try:
        os.makedirs("/etc/default", exist_ok=True)
        with open("/etc/default/grub", "w") as f:
            f.write(content)
        _log("Wrote GRUB configuration")
    except Exception as e:
        _log(f"Failed to write GRUB config: {e}", "ERROR")


def install_grub_bios(device):
    try:
        subprocess.run(
            ["grub-install", f"--target=i386-pc", f"--boot-directory=/boot", f"/dev/{device}"],
            check=True, capture_output=True, timeout=120
        )
        subprocess.run(
            ["update-grub"], check=True, capture_output=True, timeout=60
        )
        _log(f"BIOS GRUB installed on /dev/{device}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"BIOS GRUB installation failed: {e}")


def install_grub_efi(device):
    efi_mnt = "/tmp/nexusos-efi"
    os.makedirs(efi_mnt, exist_ok=True)
    try:
        subprocess.run(
            ["mount", f"/dev/{device}1", efi_mnt],
            check=True, capture_output=True, timeout=30
        )
        os.makedirs(f"{efi_mnt}/EFI/NexusOS", exist_ok=True)
        subprocess.run(
            ["cp", "-r", "/boot/efi/EFI/BOOT", f"{efi_mnt}/EFI/NexusOS/"],
            capture_output=True, timeout=30
        )
        subprocess.run(
            ["efibootmgr", "--create", "--disk", f"/dev/{device}", "--part", "1",
             "--loader", "\\EFI\\NexusOS\\BOOT\\BOOTX64.EFI",
             "--label", "NexusOS", "--verbose"],
            check=True, capture_output=True, timeout=30
        )
        subprocess.run(["umount", efi_mnt], capture_output=True)
        _log(f"UEFI GRUB installed on /dev/{device}")
    except subprocess.CalledProcessError as e:
        subprocess.run(["umount", efi_mnt], capture_output=True)
        raise RuntimeError(f"UEFI GRUB installation failed: {e}")


def is_uefi_system():
    return os.path.isdir("/sys/firmware/efi")


def detect_boot_mode():
    if is_uefi_system():
        return "uefi"
    return "bios"


def install_dual_boot(device, partition_size_gb, steps_callback=None):
    def cb(pct, msg):
        if steps_callback:
            steps_callback(pct, msg)
        _log(f"Install [{pct}%]: {msg}")

    cb(5, "Detecting Windows partition...")
    win_part = detect_windows_partition(device)
    if not win_part:
        raise RuntimeError("No Windows partition found on target disk")

    cb(10, "Shrinking Windows partition...")
    new_win_end = float(win_part["end"].replace("GB", "")) - partition_size_gb
    if new_win_end < 20:
        raise RuntimeError("Insufficient space: Windows partition would be too small")
    try:
        if win_part["filesystem"] == "ntfs":
            remaining_bytes = int(new_win_end * 1024 * 1024 * 1024)
            subprocess.run(
                ["ntfsresize", "-f", "-s", str(remaining_bytes),
                 f"/dev/{device}{win_part['number']}"],
                check=True, capture_output=True, timeout=300
            )
        subprocess.run(
            ["parted", "-s", f"/dev/{device}", "resizepart",
             win_part["number"], f"{new_win_end}GB"],
            check=True, capture_output=True, timeout=60
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to shrink Windows partition: {e}")

    cb(30, "Creating NexusOS partition...")
    try:
        subprocess.run(
            ["parted", "-s", f"/dev/{device}", "mkpart", "primary", "ext4",
             f"{win_part['end']}", "100%"],
            check=True, capture_output=True, timeout=60
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create partition: {e}")

    new_part = get_last_partition(device)
    target = f"/dev/{device}{new_part}"

    cb(40, "Formatting Btrfs with zstd:3 compression...")
    try:
        subprocess.run(
            ["mkfs.btrfs", "-f", "-L", "NexusOS", "-O", "zstd:3", target],
            check=True, capture_output=True, timeout=120
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Btrfs format failed: {e}")

    deploy_nexusos_files(target, steps_callback)

    cb(70, "Installing kernel and initramfs...")
    try:
        _install_kernel_to_target(target)
    except Exception as e:
        _log(f"Kernel install warning: {e}", "WARN")

    cb(75, "Configuring multi-boot GRUB...")
    configure_grub_dualboot(device)

    cb(80, "Setting up fstab...")
    _configure_fstab(target, device, new_part)

    cb(85, "Installing GRUB bootloader...")
    mode = detect_boot_mode()
    if mode == "uefi":
        install_grub_efi(device)
    else:
        install_grub_bios(device)

    cb(95, "Finalizing installation...")
    _configure_initramfs(target)

    cb(100, "Installation complete!")
    _log("Dual-boot installation completed successfully")


def install_full_replace(device, steps_callback=None):
    def cb(pct, msg):
        if steps_callback:
            steps_callback(pct, msg)
        _log(f"Install [{pct}%]: {msg}")

    cb(5, "Wiping disk signatures...")
    try:
        subprocess.run(
            ["wipefs", "-a", f"/dev/{device}"],
            check=True, capture_output=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        _log(f"Wipefs warning (non-fatal): {e}", "WARN")

    cb(8, "Creating GPT partition table...")
    try:
        subprocess.run(
            ["sgdisk", "-Z", f"/dev/{device}"],
            check=True, capture_output=True, timeout=30
        )
        subprocess.run(
            ["sgdisk", "--clear", f"/dev/{device}"],
            check=True, capture_output=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create partition table: {e}")

    mode = detect_boot_mode()

    cb(12, "Creating EFI System Partition...")
    try:
        subprocess.run(
            ["sgdisk", "--new=1:0:+512M", "--typecode=1:ef00",
             "--change-name=1:EFI", f"/dev/{device}"],
            check=True, capture_output=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create ESP: {e}")

    cb(18, "Creating root partition...")
    try:
        subprocess.run(
            ["sgdisk", "--new=2:0:0", "--typecode=2:8300",
             "--change-name=2:NexusOS", f"/dev/{device}"],
            check=True, capture_output=True, timeout=30
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create root partition: {e}")

    cb(22, "Formatting partitions...")
    try:
        subprocess.run(
            ["mkfs.fat", "-F32", "-n", "EFI", f"/dev/{device}1"],
            check=True, capture_output=True, timeout=30
        )
        subprocess.run(
            ["mkfs.btrfs", "-f", "-L", "NexusOS", "-O", "zstd:3", f"/dev/{device}2"],
            check=True, capture_output=True, timeout=120
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Format failed: {e}")

    cb(28, "Creating Btrfs subvolumes...")
    create_btrfs_subvolumes(f"/dev/{device}2")

    deploy_nexusos_files(f"/dev/{device}2", steps_callback)

    cb(65, "Installing kernel and initramfs...")
    try:
        _install_kernel_to_target(f"/dev/{device}2")
    except Exception as e:
        _log(f"Kernel install warning: {e}", "WARN")

    cb(70, "Mounting EFI partition and configuring boot...")
    efi_mnt = "/tmp/nexusos-efi"
    os.makedirs(efi_mnt, exist_ok=True)
    try:
        subprocess.run(
            ["mount", f"/dev/{device}1", efi_mnt],
            check=True, capture_output=True, timeout=30
        )
        os.makedirs(f"{efi_mnt}/EFI/NexusOS", exist_ok=True)
        shutil.copytree("/boot/efi/EFI/BOOT", f"{efi_mnt}/EFI/NexusOS/BOOT",
                        dirs_exist_ok=True)
        subprocess.run(["umount", efi_mnt], capture_output=True)
    except Exception as e:
        subprocess.run(["umount", efi_mnt], capture_output=True)
        _log(f"EFI copy warning: {e}", "WARN")

    cb(75, "Configuring GRUB...")
    configure_grub_standalone(device)

    cb(80, "Setting up fstab...")
    _configure_fstab(f"/dev/{device}2", device, 2)

    cb(85, "Installing GRUB bootloader...")
    try:
        if mode == "uefi":
            install_grub_efi(device)
        else:
            install_grub_bios(device)
    except RuntimeError as e:
        _log(f"GRUB install warning: {e}", "WARN")

    cb(92, "Configuring initramfs...")
    _configure_initramfs(f"/dev/{device}2")

    cb(97, "Setting hostname and locale...")
    _configure_system_settings(f"/dev/{device}2")

    cb(100, "Installation complete!")
    _log("Full replace installation completed successfully")


def _install_kernel_to_target(target):
    os.makedirs(NEXUSOS_MOUNT, exist_ok=True)
    try:
        subprocess.run(
            ["mount", target, NEXUSOS_MOUNT],
            check=True, capture_output=True, timeout=30
        )
        subprocess.run(
            ["chroot", NEXUSOS_MOUNT, "update-initramfs", "-u"],
            capture_output=True, timeout=120
        )
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
    except subprocess.CalledProcessError as e:
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log(f"Kernel install in chroot failed: {e}", "WARN")


def _configure_fstab(target, device, part_num):
    os.makedirs(NEXUSOS_MOUNT, exist_ok=True)
    try:
        subprocess.run(
            ["mount", target, NEXUSOS_MOUNT],
            check=True, capture_output=True, timeout=30
        )
        uuid_result = subprocess.run(
            ["blkid", "-s", "UUID", "-o", "value", f"/dev/{device}{part_num}"],
            capture_output=True, text=True, timeout=10
        )
        root_uuid = uuid_result.stdout.strip()
        fstab_content = f"""UUID={root_uuid}  /          btrfs  defaults,noatime,compress=zstd:3  0  0
UUID={root_uuid}  /home      btrfs  defaults,noatime,compress=zstd:3,subvol=@home  0  0
UUID={root_uuid}  /var/log   btrfs  defaults,noatime,compress=zstd:3,subvol=@var-log  0  0
UUID={root_uuid}  /tmp       btrfs  defaults,noatime,compress=zstd:3,subvol=@tmp  0  0
UUID={root_uuid}  /.snapshots btrfs  defaults,noatime,compress=zstd:3,subvol=@snapshots  0  0
tmpfs             /tmp       tmpfs  defaults,noatime,mode=1777  0  0
"""
        efi_uuid_result = subprocess.run(
            ["blkid", "-s", "UUID", "-o", "value", f"/dev/{device}1"],
            capture_output=True, text=True, timeout=10
        )
        efi_uuid = efi_uuid_result.stdout.strip()
        if efi_uuid:
            fstab_content += f"UUID={efi_uuid}  /boot/efi  vfat  defaults,noatime  0  0\n"

        fstab_path = f"{NEXUSOS_MOUNT}/etc/fstab"
        with open(fstab_path, "w") as f:
            f.write(fstab_content)
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log("Configured /etc/fstab")
    except Exception as e:
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log(f"fstab configuration failed: {e}", "ERROR")
        raise


def _configure_initramfs(target):
    os.makedirs(NEXUSOS_MOUNT, exist_ok=True)
    try:
        subprocess.run(
            ["mount", target, NEXUSOS_MOUNT],
            check=True, capture_output=True, timeout=30
        )
        subprocess.run(
            ["chroot", NEXUSOS_MOUNT, "update-initramfs", "-u", "-k", "all"],
            capture_output=True, timeout=180
        )
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log("initramfs updated")
    except subprocess.CalledProcessError as e:
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log(f"initramfs update failed: {e}", "WARN")


def _configure_system_settings(target):
    os.makedirs(NEXUSOS_MOUNT, exist_ok=True)
    try:
        subprocess.run(
            ["mount", target, NEXUSOS_MOUNT],
            check=True, capture_output=True, timeout=30
        )
        hostname_path = f"{NEXUSOS_MOUNT}/etc/hostname"
        with open(hostname_path, "w") as f:
            f.write("nexusos\n")
        hosts_path = f"{NEXUSOS_MOUNT}/etc/hosts"
        with open(hosts_path, "w") as f:
            f.write("127.0.0.1\tlocalhost\n127.0.1.1\tnexusos\n")
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log("System settings configured")
    except Exception as e:
        subprocess.run(["umount", NEXUSOS_MOUNT], capture_output=True)
        _log(f"System settings failed: {e}", "WARN")


class SlideTransition(QPropertyAnimation):
    def __init__(self, widget, direction="left", duration=TRANSITION_MS):
        super().__init__(widget, b"geometry")
        self.setDuration(duration)
        self.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._widget = widget
        self._direction = direction

    def slide_to(self, current_rect, next_rect):
        self.setStartValue(current_rect)
        self.setEndValue(next_rect)
        self.start()


class InstallWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, install_type, device, partition_size_gb=0):
        super().__init__()
        self.install_type = install_type
        self.device = device
        self.partition_size_gb = partition_size_gb
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            if self._cancelled:
                self.finished.emit(False, "Installation cancelled")
                return

            if self.install_type == "dual_boot":
                install_dual_boot(self.device, self.partition_size_gb, self._emit_progress)
            elif self.install_type == "full_replace":
                install_full_replace(self.device, self._emit_progress)
            elif self.install_type == "usb_trial":
                self._emit_progress(10, "Preparing USB trial mode...")
                self._emit_progress(50, "Copying files to USB...")
                time.sleep(2)
                self._emit_progress(100, "USB trial ready!")

            if not self._cancelled:
                self.finished.emit(True, "Installation completed successfully")
        except Exception as e:
            _log(f"Installation failed: {e}", "ERROR")
            self.finished.emit(False, str(e))

    def _emit_progress(self, pct, msg):
        if not self._cancelled:
            self.progress.emit(pct, msg)


class GlowLabel(QLabel):
    def __init__(self, text, parent=None, glow_color=ACCENT):
        super().__init__(text, parent)
        self._glow_color = QColor(glow_color)
        self._glow_radius = 0
        self._glow_direction = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate_glow)
        self._timer.start(50)

    def _animate_glow(self):
        self._glow_radius += 2 * self._glow_direction
        if self._glow_radius >= 15:
            self._glow_direction = -1
        elif self._glow_radius <= 0:
            self._glow_direction = 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._glow_radius > 0:
            for i in range(int(self._glow_radius)):
                alpha = int(80 * (1.0 - i / max(self._glow_radius, 1)))
                color = QColor(self._glow_color)
                color.setAlpha(alpha)
                painter.setPen(QPen(color, 2 + i * 2))
                painter.drawText(self.rect(), self.alignment(), self.text())
        painter.setPen(QPen(QColor(TEXT_PRIMARY), 1))
        font = self.font()
        painter.setFont(font)
        painter.drawText(self.rect(), self.alignment(), self.text())
        painter.end()


class CardWidget(QFrame):
    clicked = pyqtSignal()

    def __init__(self, parent=None, selected=False):
        super().__init__(parent)
        self._selected = selected
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(int(SCREEN_H * 0.08))
        self.setStyleSheet(self._get_style())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def _get_style(self):
        bg = ACCENT_DIM if self._selected else (BG_CARD_HOVER if self._hovered else BG_CARD)
        border = ACCENT if self._selected else "#2A3A4A"
        return f"""
            CardWidget {{
                background-color: {bg};
                border: 2px solid {border};
                border-radius: 12px;
                padding: 16px;
            }}
        """

    def set_selected(self, selected):
        self._selected = selected
        self.setStyleSheet(self._get_style())

    def enterEvent(self, event):
        self._hovered = True
        self.setStyleSheet(self._get_style())

    def leaveEvent(self, event):
        self._hovered = False
        self.setStyleSheet(self._get_style())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class LanguageCard(CardWidget):
    def __init__(self, code, name, flag, parent=None):
        super().__init__(parent)
        self._code = code
        self._name = name
        self._flag = flag
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(8, 8, 8, 8)

        flag_label = QLabel(flag)
        flag_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sw = SCREEN_W
        flag_label.setFont(QFont("Segoe UI Emoji", int(sw * 0.025)))
        layout.addWidget(flag_label)

        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setFont(QFont("Segoe UI", int(sw * 0.011), QFont.Weight.DemiBold))
        name_label.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
        layout.addWidget(name_label)

        code_label = QLabel(code.upper())
        code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code_label.setFont(QFont("Consolas", int(sw * 0.009)))
        code_label.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
        layout.addWidget(code_label)

        self.setFixedWidth(int(SCREEN_W * 0.09))
        self.setFixedHeight(int(SCREEN_H * 0.13))

    @property
    def code(self):
        return self._code


class WizardStep(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(
            int(SCREEN_W * 0.08), int(SCREEN_H * 0.06),
            int(SCREEN_W * 0.08), int(SCREEN_H * 0.04)
        )
        self.main_layout.setSpacing(int(SCREEN_H * 0.02))


class InstallerWizard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NexusOS Installer")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        screen = QApplication.primaryScreen()
        geom = screen.geometry()
        global SCREEN_W, SCREEN_H
        SCREEN_W = geom.width()
        SCREEN_H = geom.height()
        self.resize(SCREEN_W, SCREEN_H)

        central = QWidget()
        central.setStyleSheet(f"background-color: {BG_PRIMARY};")
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.title_bar = self._create_title_bar()
        root_layout.addWidget(self.title_bar)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        self.steps = []
        self.current_step = 0
        self._transitioning = False

        self.install_type = None
        self.selected_device = None
        self.partition_size_gb = 0
        self.selected_language = "en"
        self.detected_disks = []

        self._create_steps()
        self.stack.setCurrentIndex(0)
        self.stack.currentWidget().show()

        self.worker = None

        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        _log("Installer wizard launched")

    def _create_title_bar(self):
        bar = QFrame()
        bar.setFixedHeight(int(SCREEN_H * 0.04))
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_SECONDARY};
                border-bottom: 1px solid #2A3A4A;
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(int(SCREEN_W * 0.015), 0, int(SCREEN_W * 0.015), 0)

        icon_label = QLabel("\u25C6")
        icon_label.setFont(QFont("Segoe UI", int(SCREEN_W * 0.012)))
        icon_label.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(icon_label)

        title = QLabel("NexusOS Installer")
        title.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01), QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        layout.addWidget(title)

        layout.addStretch()

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(int(SCREEN_W * 0.025), int(SCREEN_H * 0.028))
        close_btn.setFont(QFont("Segoe UI", int(SCREEN_W * 0.009)))
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {TEXT_SECONDARY};
                border: none; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {DANGER}; color: white; }}
        """)
        close_btn.clicked.connect(self._handle_close)
        layout.addWidget(close_btn)

        return bar

    def _handle_close(self):
        if self.install_type and self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Cancel Installation?",
                "Installation is in progress. Are you sure you want to cancel?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.worker.cancel()
                time.sleep(1)
                self._cleanup_on_exit()
                QApplication.quit()
        else:
            reply = QMessageBox.question(
                self, "Quit Installer?",
                "Are you sure you want to quit the installer?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._cleanup_on_exit()
                QApplication.quit()

    def _cleanup_on_exit(self):
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass

    def _create_steps(self):
        self.step_welcome = self._create_step_welcome()
        self.step_license = self._create_step_license()
        self.step_language = self._create_step_language()
        self.step_install_type = self._create_step_install_type()
        self.step_full_sub = self._create_step_full_sub()
        self.step_disk = self._create_step_disk()
        self.step_summary = self._create_step_summary()
        self.step_progress = self._create_step_progress()

        self.steps = [
            self.step_welcome, self.step_license, self.step_language,
            self.step_install_type, self.step_full_sub, self.step_disk,
            self.step_summary, self.step_progress
        ]

        for i, step in enumerate(self.steps):
            self.stack.addWidget(step)

    def _create_step_welcome(self):
        step = WizardStep()
        step.main_layout.addStretch(2)

        subtitle_top = QLabel("WELCOME TO")
        subtitle_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_top.setFont(QFont("Segoe UI", int(SCREEN_W * 0.014), QFont.Weight.Normal))
        subtitle_top.setStyleSheet(f"color: {TEXT_SECONDARY}; letter-spacing: 4px;")
        step.main_layout.addWidget(subtitle_top)

        step.main_layout.addSpacing(int(SCREEN_H * 0.01))

        glow = GlowLabel("NexusOS")
        glow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glow.setFont(QFont("Segoe UI", int(SCREEN_W * 0.055), QFont.Weight.Bold))
        glow.setMinimumHeight(int(SCREEN_H * 0.1))
        glow.setStyleSheet(f"color: {ACCENT};")
        step.main_layout.addWidget(glow)

        edition = QLabel("Gaming Edition")
        edition.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edition.setFont(QFont("Segoe UI", int(SCREEN_W * 0.022), QFont.Weight.Light))
        edition.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(edition)

        step.main_layout.addSpacing(int(SCREEN_H * 0.01))

        version = QLabel("v2.0.0 (2026.07) \u2022 Linux 6.15 \u2022 Wayland + KDE 6")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setFont(QFont("Consolas", int(SCREEN_W * 0.009)))
        version.setStyleSheet(f"color: {TEXT_DIM};")
        step.main_layout.addWidget(version)

        tagline = QLabel("Built for performance. Optimized for gaming. Made for you.")
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline.setFont(QFont("Segoe UI", int(SCREEN_W * 0.012)))
        tagline.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(tagline)

        step.main_layout.addStretch(3)

        nav = self._create_nav_bar()
        nav["back_btn"].setVisible(False)
        nav["next_btn"].setText("Get Started  \u25B6")
        nav["next_btn"].clicked.connect(lambda: self._go_to_step(2))
        step.main_layout.addLayout(nav)

        return step

    def _create_step_license(self):
        step = WizardStep()

        header = QLabel("License Agreement")
        header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {BG_SECONDARY};
                border: 1px solid #2A3A4A;
                border-radius: 8px;
            }}
            QWidget {{
                background: {BG_SECONDARY};
            }}
            QScrollBar:vertical {{
                background: {BG_PRIMARY};
                width: 10px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical {{
                background: {TEXT_DIM};
                border-radius: 5px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(int(SCREEN_H * 0.5))

        license_text = QLabel(
            "NEXUSOS GAMING EDITION \u2014 PROPRIETARY LICENSE AGREEMENT\n\n"
            "Copyright \u00A9 2024-2026 NexusOS Project. All rights reserved.\n\n"
            "IMPORTANT: READ THIS AGREEMENT CAREFULLY BEFORE USING NEXUSOS.\n\n"
            "1. GRANT OF LICENSE\n"
            "NexusOS Project grants you a non-exclusive, non-transferable license to use "
            "NexusOS Gaming Edition on a single personal computer for evaluation purposes "
            "during the trial period.\n\n"
            "2. TRIAL EDITION\n"
            "The Safe Trial Edition may be used from a USB drive for up to 30 days without "
            "activation. After the trial period, certain features may be restricted.\n\n"
            "3. RESTRICTIONS\n"
            "You may not: (a) reverse engineer, decompile, or disassemble NexusOS; "
            "(b) distribute copies to third parties; (c) use NexusOS for commercial "
            "purposes without a commercial license; (d) remove any proprietary notices.\n\n"
            "4. GAMING FEATURES\n"
            "NexusOS includes optimized drivers, Wine/Proton integration, and gaming "
            "utilities. These features are provided as-is without warranty of compatibility "
            "with specific games or hardware configurations.\n\n"
            "5. PRIVACY\n"
            "NexusOS does not collect personal data without explicit consent. Telemetry "
            "may be enabled optionally and can be disabled at any time.\n\n"
            "6. OPEN SOURCE COMPONENTS\n"
            "NexusOS includes open-source software licensed under GPL, MIT, Apache 2.0, "
            "and other compatible licenses. Source code is available at "
            "https://github.com/nexusos/sources.\n\n"
            "7. WARRANTY DISCLAIMER\n"
            "NEXUSOS IS PROVIDED \"AS IS\" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, "
            "INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, "
            "AND NONINFRINGEMENT.\n\n"
            "8. LIMITATION OF LIABILITY\n"
            "IN NO EVENT SHALL NEXUSOS PROJECT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, "
            "SPECIAL, OR CONSEQUENTIAL DAMAGES.\n\n"
            "9. GOVERNING LAW\n"
            "This agreement is governed by the laws of the applicable jurisdiction.\n\n"
            "By clicking 'I Accept', you agree to be bound by the terms of this agreement."
        )
        license_text.setFont(QFont("Consolas", int(SCREEN_W * 0.0085)))
        license_text.setStyleSheet(f"color: {TEXT_SECONDARY}; padding: 16px; line-height: 1.5;")
        license_text.setWordWrap(True)
        license_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(license_text)
        step.main_layout.addWidget(scroll)

        step.main_layout.addSpacing(int(SCREEN_H * 0.01))

        self.license_checkbox = QCheckBox("I have read and accept the License Agreement")
        self.license_checkbox.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        self.license_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: {TEXT_SECONDARY};
                spacing: 10px;
            }}
            QCheckBox::indicator {{
                width: 20px; height: 20px;
                border: 2px solid {TEXT_DIM};
                border-radius: 4px;
                background: {BG_PRIMARY};
            }}
            QCheckBox::indicator:checked {{
                background: {ACCENT};
                border-color: {ACCENT};
            }}
        """)
        self.license_checkbox.stateChanged.connect(self._on_license_toggle)
        step.main_layout.addWidget(self.license_checkbox, alignment=Qt.AlignmentFlag.AlignLeft)

        nav = self._create_nav_bar()
        nav["back_btn"].clicked.connect(lambda: self._go_to_step(0))
        nav["next_btn"].setEnabled(False)
        nav["next_btn"].clicked.connect(lambda: self._go_to_step(2))
        self.license_next_btn = nav["next_btn"]
        step.main_layout.addLayout(nav)

        return step

    def _on_license_toggle(self, state):
        self.license_next_btn.setEnabled(state == Qt.CheckState.Checked.value)

    def _create_step_language(self):
        step = WizardStep()

        header = QLabel("Select Language")
        header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(header)

        desc = QLabel("Choose your preferred language for the installer and system.")
        desc.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(desc)

        step.main_layout.addSpacing(int(SCREEN_H * 0.02))

        languages = [
            ("en", "English", "\U0001F1EC\U0001F1E7"),
            ("es", "Espa\u00F1ol", "\U0001F1EA\U0001F1F8"),
            ("fr", "Fran\u00E7ais", "\U0001F1EB\U0001F1F7"),
            ("de", "Deutsch", "\U0001F1E9\U0001F1EA"),
            ("ja", "\u65E5\u672C\u8A9E", "\U0001F1EF\U0001F1F5"),
            ("ko", "\uD55C\uAD6D\uC5B4", "\U0001F1F0\U0001F1F7"),
            ("ar", "\u0627\u0644\u0639\u0631\u0628\u064A\u0629", "\U0001F1F8\U0001F1E6"),
            ("pt", "Portugu\u00EAs", "\U0001F1E7\U0001F1F7"),
            ("ru", "\u0420\u0443\u0441\u0441\u043A\u0438\u0439", "\U0001F1F7\U0001F1FA"),
            ("zh", "\u4E2D\u6587", "\U0001F1E8\U0001F1F3"),
        ]

        grid = QGridLayout()
        grid.setSpacing(int(SCREEN_W * 0.012))
        grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.language_cards = {}

        for idx, (code, name, flag) in enumerate(languages):
            card = LanguageCard(code, name, flag)
            card.clicked.connect(partial(self._select_language, code))
            self.language_cards[code] = card
            row = idx // 5
            col = idx % 5
            grid.addWidget(card, row, col)

        self.language_cards["en"].set_selected(True)

        container = QWidget()
        container.setLayout(grid)
        container.setStyleSheet("background: transparent;")
        step.main_layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)

        step.main_layout.addStretch()

        nav = self._create_nav_bar()
        nav["back_btn"].clicked.connect(lambda: self._go_to_step(1))
        nav["next_btn"].clicked.connect(lambda: self._go_to_step(3))
        step.main_layout.addLayout(nav)

        return step

    def _select_language(self, code):
        for c in self.language_cards.values():
            c.set_selected(False)
        self.language_cards[code].set_selected(True)
        self.selected_language = code
        _log(f"Language selected: {code}")

    def _create_step_install_type(self):
        step = WizardStep()

        header = QLabel("Installation Type")
        header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(header)

        desc = QLabel("Choose how you want to deploy NexusOS on your system.")
        desc.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(desc)

        step.main_layout.addSpacing(int(SCREEN_H * 0.03))

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(int(SCREEN_W * 0.03))
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        safe_card = CardWidget()
        safe_card.setMinimumWidth(int(SCREEN_W * 0.35))
        safe_card.setMinimumHeight(int(SCREEN_H * 0.42))
        safe_layout = QVBoxLayout(safe_card)
        safe_layout.setContentsMargins(int(SCREEN_W * 0.02), int(SCREEN_H * 0.03),
                                       int(SCREEN_W * 0.02), int(SCREEN_H * 0.02))
        safe_layout.setSpacing(int(SCREEN_H * 0.01))

        icon_safe = QLabel("\U0001F4BA")
        icon_safe.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_safe.setFont(QFont("Segoe UI Emoji", int(SCREEN_W * 0.04)))
        safe_layout.addWidget(icon_safe)

        title_safe = QLabel("Safe Trial Edition")
        title_safe.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_safe.setFont(QFont("Segoe UI", int(SCREEN_W * 0.018), QFont.Weight.Bold))
        title_safe.setStyleSheet(f"color: {ACCENT}; background: transparent; border: none;")
        safe_layout.addWidget(title_safe)

        desc_safe = QLabel("Try NexusOS from USB without modifying your system.\nTest everything risk-free.")
        desc_safe.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_safe.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01)))
        desc_safe.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
        desc_safe.setWordWrap(True)
        safe_layout.addWidget(desc_safe)

        safe_layout.addSpacing(int(SCREEN_H * 0.01))

        features_safe = [
            "\u2713 Boots from USB drive",
            "\u2713 Read-only system (safe)",
            "\u2713 Test Waydroid, games, UI",
            "\u2713 No changes to your disk",
        ]
        for feat in features_safe:
            fl = QLabel(feat)
            fl.setFont(QFont("Segoe UI", int(SCREEN_W * 0.0095)))
            fl.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
            fl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            safe_layout.addWidget(fl)

        safe_layout.addStretch()
        safe_card.clicked.connect(lambda: self._select_install_type("usb_trial"))
        self.safe_card_ref = safe_card

        full_card = CardWidget()
        full_card.setMinimumWidth(int(SCREEN_W * 0.35))
        full_card.setMinimumHeight(int(SCREEN_H * 0.42))
        full_layout = QVBoxLayout(full_card)
        full_layout.setContentsMargins(int(SCREEN_W * 0.02), int(SCREEN_H * 0.03),
                                       int(SCREEN_W * 0.02), int(SCREEN_H * 0.02))
        full_layout.setSpacing(int(SCREEN_H * 0.01))

        icon_full = QLabel("\U0001F4BD")
        icon_full.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_full.setFont(QFont("Segoe UI Emoji", int(SCREEN_W * 0.04)))
        full_layout.addWidget(icon_full)

        title_full = QLabel("Full Installation")
        title_full.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_full.setFont(QFont("Segoe UI", int(SCREEN_W * 0.018), QFont.Weight.Bold))
        title_full.setStyleSheet(f"color: {ACCENT}; background: transparent; border: none;")
        full_layout.addWidget(title_full)

        desc_full = QLabel("Install NexusOS permanently. Choose dual-boot or\nreplace your OS entirely.")
        desc_full.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_full.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01)))
        desc_full.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
        desc_full.setWordWrap(True)
        full_layout.addWidget(desc_full)

        full_layout.addSpacing(int(SCREEN_H * 0.01))

        features_full = [
            "\u2713 Dual-boot with Windows",
            "\u2713 Or replace Windows entirely",
            "\u2713 Full performance",
            "\u2713 Persistent storage",
        ]
        for feat in features_full:
            fl = QLabel(feat)
            fl.setFont(QFont("Segoe UI", int(SCREEN_W * 0.0095)))
            fl.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
            fl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            full_layout.addWidget(fl)

        full_layout.addStretch()
        full_card.clicked.connect(lambda: self._select_install_type("full_install"))
        self.full_card_ref = full_card

        cards_layout.addWidget(safe_card, alignment=Qt.AlignmentFlag.AlignCenter)
        cards_layout.addWidget(full_card, alignment=Qt.AlignmentFlag.AlignCenter)
        step.main_layout.addLayout(cards_layout)

        step.main_layout.addStretch()

        nav = self._create_nav_bar()
        nav["back_btn"].clicked.connect(lambda: self._go_to_step(2))
        nav["next_btn"].setVisible(False)
        self.install_type_next_nav = nav
        step.main_layout.addLayout(nav)

        return step

    def _select_install_type(self, itype):
        self.install_type = itype
        self.safe_card_ref.set_selected(itype == "usb_trial")
        self.full_card_ref.set_selected(itype == "full_install")

        self.install_type_next_nav["next_btn"].setVisible(True)
        self.install_type_next_nav["next_btn"].disconnect()

        if itype == "usb_trial":
            self.install_type_next_nav["next_btn"].setText("Start Trial  \u25B6")
            self.install_type_next_nav["next_btn"].clicked.connect(
                lambda: self._go_to_step(6)
            )
        elif itype == "full_install":
            self.install_type_next_nav["next_btn"].setText("Continue  \u25B6")
            self.install_type_next_nav["next_btn"].clicked.connect(
                lambda: self._go_to_step(4)
            )

        _log(f"Install type selected: {itype}")

    def _create_step_full_sub(self):
        step = WizardStep()

        header = QLabel("Full Installation Method")
        header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(header)

        desc = QLabel("Choose how to install NexusOS on your disk.")
        desc.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(desc)

        step.main_layout.addSpacing(int(SCREEN_H * 0.03))

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(int(SCREEN_W * 0.03))
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        dual_card = CardWidget()
        dual_card.setMinimumWidth(int(SCREEN_W * 0.35))
        dual_card.setMinimumHeight(int(SCREEN_H * 0.35))
        dual_layout = QVBoxLayout(dual_card)
        dual_layout.setContentsMargins(int(SCREEN_W * 0.025), int(SCREEN_H * 0.03),
                                       int(SCREEN_W * 0.025), int(SCREEN_H * 0.02))
        dual_layout.setSpacing(int(SCREEN_H * 0.01))

        icon_dual = QLabel("\U0001F504")
        icon_dual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_dual.setFont(QFont("Segoe UI Emoji", int(SCREEN_W * 0.035)))
        dual_layout.addWidget(icon_dual)

        title_dual = QLabel("Dual-Boot (Safe)")
        title_dual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_dual.setFont(QFont("Segoe UI", int(SCREEN_W * 0.018), QFont.Weight.Bold))
        title_dual.setStyleSheet(f"color: {ACCENT}; background: transparent; border: none;")
        dual_layout.addWidget(title_dual)

        desc_dual = QLabel(
            "Shrink existing partition, install NexusOS alongside Windows.\n"
            "Both operating systems will be accessible at boot."
        )
        desc_dual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_dual.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01)))
        desc_dual.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
        desc_dual.setWordWrap(True)
        dual_layout.addWidget(desc_dual)

        dual_layout.addStretch()
        dual_card.clicked.connect(lambda: self._select_full_sub("dual_boot"))
        self.dual_card_ref = dual_card

        replace_card = CardWidget()
        replace_card.setMinimumWidth(int(SCREEN_W * 0.35))
        replace_card.setMinimumHeight(int(SCREEN_H * 0.35))
        replace_layout = QVBoxLayout(replace_card)
        replace_layout.setContentsMargins(int(SCREEN_W * 0.025), int(SCREEN_H * 0.03),
                                          int(SCREEN_W * 0.025), int(SCREEN_H * 0.02))
        replace_layout.setSpacing(int(SCREEN_H * 0.01))

        icon_replace = QLabel("\u26A0\uFE0F")
        icon_replace.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_replace.setFont(QFont("Segoe UI Emoji", int(SCREEN_W * 0.035)))
        replace_layout.addWidget(icon_replace)

        title_replace = QLabel("Full Replace")
        title_replace.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_replace.setFont(QFont("Segoe UI", int(SCREEN_W * 0.018), QFont.Weight.Bold))
        title_replace.setStyleSheet(f"color: {DANGER}; background: transparent; border: none;")
        replace_layout.addWidget(title_replace)

        desc_replace = QLabel(
            "Erase entire disk. NexusOS becomes your only operating system.\n"
            "All data on the target disk will be permanently destroyed."
        )
        desc_replace.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_replace.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01)))
        desc_replace.setStyleSheet(f"color: {WARNING}; background: transparent; border: none;")
        desc_replace.setWordWrap(True)
        replace_layout.addWidget(desc_replace)

        replace_layout.addStretch()
        replace_card.clicked.connect(lambda: self._select_full_sub("full_replace"))
        self.replace_card_ref = replace_card

        cards_layout.addWidget(dual_card, alignment=Qt.AlignmentFlag.AlignCenter)
        cards_layout.addWidget(replace_card, alignment=Qt.AlignmentFlag.AlignCenter)
        step.main_layout.addLayout(cards_layout)

        step.main_layout.addStretch()

        nav = self._create_nav_bar()
        nav["back_btn"].clicked.connect(lambda: self._go_to_step(3))
        nav["next_btn"].setVisible(False)
        self.full_sub_next_nav = nav
        step.main_layout.addLayout(nav)

        return step

    def _select_full_sub(self, subtype):
        if subtype == "dual_boot":
            self.install_type = "dual_boot"
            self.dual_card_ref.set_selected(True)
            self.replace_card_ref.set_selected(False)
        else:
            self.install_type = "full_replace"
            self.dual_card_ref.set_selected(False)
            self.replace_card_ref.set_selected(True)

        self.full_sub_next_nav["next_btn"].setVisible(True)
        self.full_sub_next_nav["next_btn"].disconnect()
        self.full_sub_next_nav["next_btn"].setText("Continue  \u25B6")
        self.full_sub_next_nav["next_btn"].clicked.connect(
            lambda: self._go_to_step(5)
        )
        _log(f"Full install sub-type: {subtype}")

    def _create_step_disk(self):
        step = WizardStep()

        self.disk_header = QLabel("Select Target Disk")
        self.disk_header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        self.disk_header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(self.disk_header)

        self.disk_desc = QLabel("Select the disk where NexusOS will be installed.")
        self.disk_desc.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        self.disk_desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(self.disk_desc)

        step.main_layout.addSpacing(int(SCREEN_H * 0.01))

        self.disk_scroll = QScrollArea()
        self.disk_scroll.setWidgetResizable(True)
        self.disk_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {BG_PRIMARY};
                border: none;
            }}
            QScrollBar:vertical {{
                background: {BG_PRIMARY};
                width: 10px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical {{
                background: {TEXT_DIM};
                border-radius: 5px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self.disk_container = QWidget()
        self.disk_container_layout = QVBoxLayout(self.disk_container)
        self.disk_container_layout.setContentsMargins(0, 0, 0, 0)
        self.disk_container_layout.setSpacing(int(SCREEN_H * 0.01))
        self.disk_container_layout.addStretch()
        self.disk_scroll.setWidget(self.disk_container)
        step.main_layout.addWidget(self.disk_scroll)

        self.warning_frame = QFrame()
        self.warning_frame.setStyleSheet(f"""
            QFrame {{
                background: #331111;
                border: 2px solid {DANGER};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        warning_layout = QVBoxLayout(self.warning_frame)
        warning_label = QLabel("\u26A0\uFE0F  DANGER: This will ERASE ALL DATA on the selected disk!")
        warning_label.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011), QFont.Weight.Bold))
        warning_label.setStyleSheet(f"color: {DANGER}; background: transparent; border: none;")
        warning_label.setWordWrap(True)
        warning_layout.addWidget(warning_label)

        erase_label = QLabel("Type ERASE to confirm:")
        erase_label.setFont(QFont("Segoe UI", int(SCREEN_W * 0.009)))
        erase_label.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
        warning_layout.addWidget(erase_label)

        erase_row = QHBoxLayout()
        self.erase_input = QLineEdit()
        self.erase_input.setFont(QFont("Consolas", int(SCREEN_W * 0.011)))
        self.erase_input.setPlaceholderText("Type ERASE here...")
        self.erase_input.setStyleSheet(f"""
            QLineEdit {{
                background: {BG_PRIMARY};
                color: {TEXT_PRIMARY};
                border: 1px solid {TEXT_DIM};
                border-radius: 4px;
                padding: 8px;
            }}
        """)
        self.erase_input.textChanged.connect(self._check_erase_input)
        erase_row.addWidget(self.erase_input)

        self.erase_btn = QPushButton("Confirm Erase")
        self.erase_btn.setEnabled(False)
        self.erase_btn.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01), QFont.Weight.Bold))
        self.erase_btn.setStyleSheet(f"""
            QPushButton {{
                background: {DANGER}; color: white;
                border: none; border-radius: 6px;
                padding: 10px 20px;
            }}
            QPushButton:disabled {{
                background: {TEXT_DIM}; color: {BG_PRIMARY};
            }}
        """)
        self.erase_btn.clicked.connect(self._confirm_erase)
        erase_row.addWidget(self.erase_btn)
        warning_layout.addLayout(erase_row)

        self.warning_frame.hide()
        step.main_layout.addWidget(self.warning_frame)

        self.dual_boot_options = QFrame()
        self.dual_boot_options.setStyleSheet(f"""
            QFrame {{
                background: {BG_SECONDARY};
                border: 1px solid #2A3A4A;
                border-radius: 8px;
                padding: 16px;
            }}
        """)
        dual_opt_layout = QVBoxLayout(self.dual_boot_options)

        size_label = QLabel("Partition Size for NexusOS:")
        size_label.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011), QFont.Weight.Bold))
        size_label.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
        dual_opt_layout.addWidget(size_label)

        slider_row = QHBoxLayout()
        self.partition_slider = QSlider(Qt.Orientation.Horizontal)
        self.partition_slider.setMinimum(50)
        self.partition_slider.setMaximum(400)
        self.partition_slider.setValue(100)
        self.partition_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 6px;
                background: {BG_PRIMARY};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT};
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT_DIM};
                border-radius: 3px;
            }}
        """)
        self.partition_slider.valueChanged.connect(self._on_slider_change)
        slider_row.addWidget(self.partition_slider, stretch=1)

        self.slider_value_label = QLabel("100 GB")
        self.slider_value_label.setFont(QFont("Consolas", int(SCREEN_W * 0.012), QFont.Weight.Bold))
        self.slider_value_label.setStyleSheet(f"color: {ACCENT}; background: transparent; border: none;")
        self.slider_value_label.setMinimumWidth(int(SCREEN_W * 0.08))
        slider_row.addWidget(self.slider_value_label)

        dual_opt_layout.addLayout(slider_row)

        free_label = QLabel("")
        free_label.setFont(QFont("Consolas", int(SCREEN_W * 0.009)))
        free_label.setStyleSheet(f"color: {TEXT_DIM}; background: transparent; border: none;")
        dual_opt_layout.addWidget(free_label)
        self.free_space_label = free_label

        self.dual_boot_options.hide()
        step.main_layout.addWidget(self.dual_boot_options)

        step.main_layout.addStretch()

        nav = self._create_nav_bar()
        nav["back_btn"].clicked.connect(lambda: self._go_to_step(4 if self.install_type in ("dual_boot", "full_replace") else 3))
        nav["next_btn"].setEnabled(False)
        nav["next_btn"].setText("Review  \u25B6")
        nav["next_btn"].clicked.connect(lambda: self._go_to_step(6))
        self.disk_next_btn = nav["next_btn"]
        step.main_layout.addLayout(nav)

        return step

    def _on_slider_change(self, value):
        self.slider_value_label.setText(f"{value} GB")
        self.partition_size_gb = float(value)

    def _check_erase_input(self, text):
        self.erase_btn.setEnabled(text.strip().upper() == "ERASE")

    def _confirm_erase(self):
        if self.selected_device:
            self.disk_next_btn.setEnabled(True)
            self.warning_frame.hide()
            _log(f"Erase confirmed for /dev/{self.selected_device}")

    def _populate_disks(self):
        while self.disk_container_layout.count():
            item = self.disk_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.detected_disks = detect_disks()

        if not self.detected_disks:
            no_disk = QLabel("No disks detected. Please connect a storage device.")
            no_disk.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_disk.setFont(QFont("Segoe UI", int(SCREEN_W * 0.012)))
            no_disk.setStyleSheet(f"color: {WARNING};")
            self.disk_container_layout.addWidget(no_disk)
            self.disk_container_layout.addStretch()
            return

        if self.install_type in ("usb_trial",):
            self.disk_header.setText("Select USB Drive")
            self.disk_desc.setText("Choose the USB drive to use for the live session.")
        elif self.install_type == "full_replace":
            self.disk_header.setText("Select Target Disk (DANGER)")
            self.disk_desc.setText("Choose the disk to ERASE and install NexusOS. ALL DATA WILL BE LOST.")
        else:
            self.disk_header.setText("Select Target Disk")
            self.disk_desc.setText("Choose the disk where NexusOS will be installed alongside Windows.")

        for disk in self.detected_disks:
            card = CardWidget()
            card.setMinimumHeight(int(SCREEN_H * 0.08))
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(int(SCREEN_W * 0.02), int(SCREEN_H * 0.01),
                                           int(SCREEN_W * 0.02), int(SCREEN_H * 0.01))

            disk_icon = QLabel("\U0001F4BD" if is_device_removable(disk["name"]) else "\U0001F4BE")
            disk_icon.setFont(QFont("Segoe UI Emoji", int(SCREEN_W * 0.02)))
            disk_icon.setStyleSheet("background: transparent; border: none;")
            card_layout.addWidget(disk_icon)

            info_layout = QVBoxLayout()
            info_layout.setSpacing(2)

            disk_name = QLabel(f"/dev/{disk['name']}")
            disk_name.setFont(QFont("Consolas", int(SCREEN_W * 0.011), QFont.Weight.Bold))
            disk_name.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
            info_layout.addWidget(disk_name)

            model_size = QLabel(f"{disk['model']}  \u2022  {disk['size_gb']:.1f} GB  \u2022  {disk['fstype'] or 'unformatted'}")
            model_size.setFont(QFont("Segoe UI", int(SCREEN_W * 0.0085)))
            model_size.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
            info_layout.addWidget(model_size)

            card_layout.addLayout(info_layout, stretch=1)
            card_layout.addStretch()

            if self.install_type == "full_replace" and disk["name"].startswith("loop"):
                continue

            card.clicked.connect(partial(self._select_disk, disk["name"], card))
            self.disk_container_layout.addWidget(card)

        self.disk_container_layout.addStretch()

    def _select_disk(self, name, card_widget):
        for i in range(self.disk_container_layout.count()):
            item = self.disk_container_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), CardWidget):
                item.widget().set_selected(False)

        card_widget.set_selected(True)
        self.selected_device = name

        if self.install_type == "full_replace":
            self.warning_frame.show()
            self.dual_boot_options.hide()
            self.disk_next_btn.setEnabled(False)
            self.erase_input.clear()
        elif self.install_type == "dual_boot":
            self.warning_frame.hide()
            self.dual_boot_options.show()
            self.disk_next_btn.setEnabled(True)

            win_part = detect_windows_partition(name)
            if not win_part:
                self.disk_desc.setText(
                    f"Warning: No Windows partition detected on /dev/{name}. "
                    "Dual-boot requires an existing Windows installation."
                )
                self.disk_desc.setStyleSheet(f"color: {WARNING};")
            else:
                self.disk_desc.setText(
                    f"Windows detected on /dev/{name}{win_part['number']} "
                    f"({win_part['filesystem'].upper()}, {win_part['size_gb']:.1f} GB)"
                )
                self.disk_desc.setStyleSheet(f"color: {TEXT_SECONDARY};")

            free_gb = detect_free_space(name)
            max_slider = max(50, int(free_gb * 0.8))
            self.partition_slider.setMaximum(max_slider)
            self.partition_slider.setValue(min(100, max_slider))
            self.free_space_label.setText(
                f"Available free space: ~{free_gb:.1f} GB  |  "
                f"Partition range: 50 \u2013 {max_slider} GB"
            )
        else:
            self.warning_frame.hide()
            self.dual_boot_options.hide()
            self.disk_next_btn.setEnabled(True)

        _log(f"Disk selected: /dev/{name}")


    def _create_step_summary(self):
        step = WizardStep()

        header = QLabel("Installation Summary")
        header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(header)

        desc = QLabel("Review your configuration before proceeding.")
        desc.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(desc)

        step.main_layout.addSpacing(int(SCREEN_H * 0.02))

        self.summary_frame = QFrame()
        self.summary_frame.setStyleSheet(f"""
            QFrame {{
                background: {BG_SECONDARY};
                border: 1px solid #2A3A4A;
                border-radius: 12px;
                padding: 24px;
            }}
        """)
        self.summary_layout = QVBoxLayout(self.summary_frame)
        self.summary_layout.setSpacing(int(SCREEN_H * 0.015))
        step.main_layout.addWidget(self.summary_frame)

        step.main_layout.addSpacing(int(SCREEN_H * 0.01))

        warning = QLabel(
            "\u26A0\uFE0F  This will modify your disk. A backup is recommended."
        )
        warning.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011), QFont.Weight.DemiBold))
        warning.setStyleSheet(f"color: {WARNING};")
        warning.setWordWrap(True)
        step.main_layout.addWidget(warning)

        step.main_layout.addStretch()

        nav = self._create_nav_bar()
        nav["back_btn"].clicked.connect(lambda: self._go_to_step(5))
        nav["next_btn"].setText("\u25B6  Install NexusOS")
        nav["next_btn"].setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT_DIM}, stop:1 {ACCENT});
                color: {BG_PRIMARY};
                border: none; border-radius: 8px;
                padding: 12px 32px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT}, stop:1 #33DDFF);
            }}
        """)
        nav["next_btn"].clicked.connect(self._start_installation)
        step.main_layout.addLayout(nav)

        return step

    def _populate_summary(self):
        while self.summary_layout.count():
            item = self.summary_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        lang_names = {
            "en": "English", "es": "Espa\u00F1ol", "fr": "Fran\u00E7ais",
            "de": "Deutsch", "ja": "\u65E5\u672C\u8A9E", "ko": "\uD55C\uAD6D\uC5B4",
            "ar": "\u0627\u0644\u0639\u0631\u0628\u064A\u0629", "pt": "Portugu\u00EAs",
            "ru": "\u0420\u0443\u0441\u0441\u043A\u0438\u0439", "zh": "\u4E2D\u6587",
        }

        type_labels = {
            "usb_trial": "Safe Trial Edition (USB)",
            "dual_boot": "Full Install \u2014 Dual-Boot",
            "full_replace": "Full Install \u2014 Replace Disk",
        }

        summary_items = [
            ("Language", lang_names.get(self.selected_language, self.selected_language)),
            ("Installation Type", type_labels.get(self.install_type, self.install_type)),
            ("Target Disk", f"/dev/{self.selected_device}" if self.selected_device else "Not selected"),
            ("Boot Mode", detect_boot_mode().upper()),
        ]

        if self.install_type == "dual_boot":
            summary_items.append(("Partition Size", f"{self.partition_size_gb:.0f} GB"))
            win_part = detect_windows_partition(self.selected_device) if self.selected_device else None
            if win_part:
                summary_items.append(("Windows Partition", f"/dev/{self.selected_device}{win_part['number']} ({win_part['filesystem'].upper()})"))

        summary_items.append(("Filesystem", "Btrfs (zstd:3 compression)"))

        if self.install_type in ("dual_boot", "full_replace"):
            disk = next((d for d in self.detected_disks if d["name"] == self.selected_device), None)
            if disk:
                summary_items.append(("Target Disk Size", f"{disk['size_gb']:.1f} GB \u2014 {disk['model']}"))

        for label, value in summary_items:
            row = QHBoxLayout()
            row.setSpacing(int(SCREEN_W * 0.02))

            lbl = QLabel(f"{label}:")
            lbl.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011), QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
            lbl.setFixedWidth(int(SCREEN_W * 0.15))
            row.addWidget(lbl)

            val = QLabel(value)
            val.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
            val.setStyleSheet(f"color: {TEXT_PRIMARY}; background: transparent; border: none;")
            row.addWidget(val)

            row.addStretch()
            self.summary_layout.addLayout(row)

        self.summary_layout.addStretch()

    def _create_step_progress(self):
        step = WizardStep()

        self.progress_header = QLabel("Installing NexusOS")
        self.progress_header.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        self.progress_header.setStyleSheet(f"color: {TEXT_PRIMARY};")
        step.main_layout.addWidget(self.progress_header)

        self.progress_desc = QLabel("Please wait while NexusOS is being installed...")
        self.progress_desc.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        self.progress_desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        step.main_layout.addWidget(self.progress_desc)

        step.main_layout.addSpacing(int(SCREEN_H * 0.02))

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setFixedHeight(int(SCREEN_H * 0.035))
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {BG_SECONDARY};
                border: 1px solid #2A3A4A;
                border-radius: 8px;
                text-align: center;
                color: {TEXT_PRIMARY};
                font-family: Consolas;
                font-size: {int(SCREEN_W * 0.009)}pt;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT_DIM}, stop:1 {ACCENT});
                border-radius: 7px;
            }}
        """)
        step.main_layout.addWidget(self.progress_bar)

        self.progress_status = QLabel("Initializing...")
        self.progress_status.setFont(QFont("Consolas", int(SCREEN_W * 0.009)))
        self.progress_status.setStyleSheet(f"color: {ACCENT};")
        step.main_layout.addWidget(self.progress_status)

        step.main_layout.addSpacing(int(SCREEN_H * 0.01))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", int(SCREEN_W * 0.008)))
        self.log_output.setStyleSheet(f"""
            QTextEdit {{
                background: {BG_SECONDARY};
                color: {TEXT_SECONDARY};
                border: 1px solid #2A3A4A;
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        self.log_output.setMinimumHeight(int(SCREEN_H * 0.25))
        step.main_layout.addWidget(self.log_output)

        step.main_layout.addStretch()

        self.complete_frame = QFrame()
        self.complete_frame.setStyleSheet(f"""
            QFrame {{
                background: {BG_SECONDARY};
                border: 2px solid {SUCCESS};
                border-radius: 12px;
                padding: 24px;
            }}
        """)
        complete_layout = QVBoxLayout(self.complete_frame)
        complete_layout.setSpacing(int(SCREEN_H * 0.015))

        complete_icon = QLabel("\u2705")
        complete_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        complete_icon.setFont(QFont("Segoe UI Emoji", int(SCREEN_W * 0.04)))
        complete_layout.addWidget(complete_icon)

        complete_title = QLabel("Installation Complete!")
        complete_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        complete_title.setFont(QFont("Segoe UI", int(SCREEN_W * 0.02), QFont.Weight.Bold))
        complete_title.setStyleSheet(f"color: {SUCCESS}; background: transparent; border: none;")
        complete_layout.addWidget(complete_title)

        complete_msg = QLabel("Remove the installation media and restart your computer.\nYour NexusOS system is ready to use!")
        complete_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        complete_msg.setFont(QFont("Segoe UI", int(SCREEN_W * 0.011)))
        complete_msg.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent; border: none;")
        complete_msg.setWordWrap(True)
        complete_layout.addWidget(complete_msg)

        reboot_btn = QPushButton("\U0001F504  Reboot Now")
        reboot_btn.setFont(QFont("Segoe UI", int(SCREEN_W * 0.012), QFont.Weight.Bold))
        reboot_btn.setMinimumWidth(int(SCREEN_W * 0.15))
        reboot_btn.setMinimumHeight(int(SCREEN_H * 0.045))
        reboot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reboot_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ACCENT};
                color: {BG_PRIMARY};
                border: none; border-radius: 8px;
                padding: 12px 32px;
            }}
            QPushButton:hover {{
                background: #33DDFF;
            }}
        """)
        reboot_btn.clicked.connect(self._reboot_system)
        complete_layout.addWidget(reboot_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.complete_frame.hide()
        step.main_layout.addWidget(self.complete_frame)

        return step

    def _create_nav_bar(self):
        nav = QHBoxLayout()
        nav.setContentsMargins(0, int(SCREEN_H * 0.02), 0, 0)

        back_btn = QPushButton("\u25C0  Back")
        back_btn.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01)))
        back_btn.setMinimumWidth(int(SCREEN_W * 0.1))
        back_btn.setMinimumHeight(int(SCREEN_H * 0.04))
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet(f"""
            QPushButton {{
                background: {BG_SECONDARY};
                color: {TEXT_SECONDARY};
                border: 1px solid #2A3A4A;
                border-radius: 8px;
                padding: 10px 20px;
            }}
            QPushButton:hover {{
                background: {BG_CARD_HOVER};
                color: {TEXT_PRIMARY};
            }}
        """)

        nav.addStretch()

        next_btn = QPushButton("Next  \u25B6")
        next_btn.setFont(QFont("Segoe UI", int(SCREEN_W * 0.01), QFont.Weight.Bold))
        next_btn.setMinimumWidth(int(SCREEN_W * 0.12))
        next_btn.setMinimumHeight(int(SCREEN_H * 0.04))
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT_DIM}, stop:1 {ACCENT});
                color: {BG_PRIMARY};
                border: none; border-radius: 8px;
                padding: 10px 24px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {ACCENT}, stop:1 #33DDFF);
            }}
            QPushButton:disabled {{
                background: {TEXT_DIM}; color: {BG_PRIMARY};
            }}
        """)

        nav.addWidget(back_btn)
        nav.addSpacing(int(SCREEN_W * 0.01))
        nav.addWidget(next_btn)

        return {"back_btn": back_btn, "next_btn": next_btn}

    def _go_to_step(self, index):
        if self._transitioning:
            return
        if index < 0 or index >= len(self.steps):
            return

        if index == 5 and self.install_type in ("dual_boot", "full_replace"):
            self._populate_disks()
        if index == 6:
            self._populate_summary()

        self._transitioning = True
        current_widget = self.stack.currentWidget()
        next_widget = self.steps[index]

        start_geo = current_widget.geometry()
        if index > self.current_step:
            end_geo = QRect(SCREEN_W, start_geo.y(), start_geo.width(), start_geo.height())
            start_offscreen = QRect(-SCREEN_W, start_geo.y(), start_geo.width(), start_geo.height())
        else:
            end_geo = QRect(-SCREEN_W, start_geo.y(), start_geo.width(), start_geo.height())
            start_offscreen = QRect(SCREEN_W, start_geo.y(), start_geo.width(), start_geo.height())

        next_widget.setGeometry(start_offscreen)
        next_widget.show()
        next_widget.raise_()

        anim_group = QParallelAnimationGroup(self)

        slide_out = QPropertyAnimation(current_widget, b"geometry")
        slide_out.setDuration(TRANSITION_MS)
        slide_out.setEasingCurve(QEasingCurve.Type.InOutCubic)
        slide_out.setStartValue(start_geo)
        slide_out.setEndValue(end_geo)
        anim_group.addAnimation(slide_out)

        slide_in = QPropertyAnimation(next_widget, b"geometry")
        slide_in.setDuration(TRANSITION_MS)
        slide_in.setEasingCurve(QEasingCurve.Type.InOutCubic)
        slide_in.setStartValue(start_offscreen)
        slide_in.setEndValue(start_geo)
        anim_group.addAnimation(slide_in)

        def on_anim_done():
            self.stack.setCurrentIndex(index)
            current_widget.hide()
            next_widget.setGeometry(start_geo)
            self.current_step = index
            self._transitioning = False

        anim_group.finished.connect(on_anim_done)
        anim_group.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim_group = anim_group

    def _start_installation(self):
        if not self.selected_device:
            QMessageBox.warning(self, "No Disk Selected", "Please go back and select a target disk.")
            return

        if self.install_type in ("dual_boot", "full_replace"):
            reply = QMessageBox.warning(
                self, "Confirm Installation",
                f"This will modify /dev/{self.selected_device}.\n\n"
                "Are you absolutely sure you want to proceed?\n"
                "A backup is strongly recommended.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.log_output.clear()
        self.progress_bar.setValue(0)
        self.progress_status.setText("Starting installation...")
        self.complete_frame.hide()

        _log(f"Starting installation: type={self.install_type}, disk={self.selected_device}")

        if self.install_type == "usb_trial":
            self.worker = InstallWorker("usb_trial", self.selected_device, 0)
        elif self.install_type == "dual_boot":
            self.worker = InstallWorker("dual_boot", self.selected_device, self.partition_size_gb)
        elif self.install_type == "full_replace":
            self.worker = InstallWorker("full_replace", self.selected_device, 0)
        else:
            return

        self.worker.progress.connect(self._on_install_progress)
        self.worker.finished.connect(self._on_install_finished)
        self.worker.start()

        _log("Installation worker started")

    @pyqtSlot(int, str)
    def _on_install_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self.progress_status.setText(msg)
        timestamp = time.strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {msg}")
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @pyqtSlot(bool, str)
    def _on_install_finished(self, success, message):
        if success:
            self.progress_bar.setValue(100)
            self.progress_status.setText("Installation complete!")
            self.progress_header.setText("Installation Complete!")
            self.progress_desc.hide()
            self.complete_frame.show()
            _log("Installation completed successfully")
        else:
            self.progress_status.setText(f"Error: {message}")
            QMessageBox.critical(
                self, "Installation Failed",
                f"An error occurred during installation:\n\n{message}\n\n"
                "Check /var/log/nexusos/installer.log for details."
            )
            _log(f"Installation failed: {message}", "ERROR")

    def _reboot_system(self):
        reply = QMessageBox.question(
            self, "Reboot System",
            "Ready to reboot? Make sure to remove the installation media.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            _log("User initiated reboot")
            try:
                subprocess.run(["systemctl", "reboot"], check=True, timeout=10)
            except Exception:
                try:
                    subprocess.run(["reboot"], check=True, timeout=10)
                except Exception as e:
                    _log(f"Reboot failed: {e}", "ERROR")
                    QMessageBox.warning(self, "Reboot Failed",
                                        "Could not reboot automatically. Please reboot manually.")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._handle_close()
        elif event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < self.title_bar.height():
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if hasattr(self, '_drag_pos') and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if hasattr(self, '_drag_pos'):
            del self._drag_pos


def acquire_lock():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                return False
            except (OSError, ValueError):
                os.remove(LOCK_FILE)
        os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(LOCK_FILE)
    except Exception:
        pass


def main():
    if not acquire_lock():
        print("Another instance is already running. Exiting.")
        sys.exit(1)

    signal.signal(signal.SIGTERM, lambda *_: (release_lock(), sys.exit(0)))

    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except Exception:
        pass

    _log("NexusOS Installer starting")

    app = QApplication(sys.argv)
    app.setApplicationName("NexusOS Installer")
    app.setOrganizationName("NexusOS")

    app.setStyleSheet(f"""
        * {{
            font-family: "Segoe UI", sans-serif;
        }}
        QMainWindow {{
            background-color: {BG_PRIMARY};
        }}
        QToolTip {{
            background-color: {BG_SECONDARY};
            color: {TEXT_PRIMARY};
            border: 1px solid {ACCENT};
            padding: 4px;
            border-radius: 4px;
        }}
    """)

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = InstallerWizard()

    exit_code = app.exec()
    release_lock()
    _log(f"Installer exiting with code {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
