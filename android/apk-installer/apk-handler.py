#!/usr/bin/env python3
import sys
import os
import subprocess
import shutil
import zipfile
import json
import struct
import logging
import tempfile
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QFrame, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QPixmap, QImage, QIcon, QColor, QFont

LOG_DIR = Path("/var/log/nexusos")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "apk-installer.log"

GAME_GRID_PATH = Path.home() / ".config" / "nexusos" / "game-grid.json"
APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"
NEXUS_ICONS_DIR = Path.home() / ".local" / "share" / "nexusos" / "icons"
CACHE_DIR = Path.home() / ".cache" / "nexusos" / "apk-installer"

NEXOS_DARK_BG = "#0d0d12"
NEXOS_PANEL_BG = "#161622"
NEXOS_ACCENT = "#6c5ce7"
NEXOS_ACCENT_HOVER = "#7d6ff0"
NEXOS_TEXT = "#e8e8f0"
NEXOS_TEXT_DIM = "#8888a0"
NEXOS_SUCCESS = "#00d2a0"
NEXOS_ERROR = "#ff4444"
NEXOS_BORDER = "#2a2a3a"

APK_MAGIC = b"\x50\x4b\x03\x04"
APK_MIME = "application/vnd.android.package-archive"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("apk-installer")


class NexusFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {NEXOS_PANEL_BG};
                border: 1px solid {NEXOS_BORDER};
                border-radius: 8px;
            }}
        """)


class NexusButton(QPushButton):
    def __init__(self, text, accent=False, parent=None):
        super().__init__(text, parent)
        bg = NEXOS_ACCENT if accent else "transparent"
        hover = NEXOS_ACCENT_HOVER if accent else NEXOS_BORDER
        border = "none" if accent else f"1px solid {NEXOS_BORDER}"
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {NEXOS_TEXT};
                border: {border};
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:pressed {{
                background-color: {NEXOS_ACCENT};
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(38)


class InstallWindow(QMainWindow):
    def __init__(self, apk_path: str):
        super().__init__()
        self.apk_path = Path(apk_path)
        self.worker = None
        self.setWindowTitle("NexusOS APK Installer")
        self.setMinimumSize(520, 400)
        self.setMaximumSize(520, 400)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        central = QWidget()
        central.setStyleSheet(f"background-color: {NEXOS_DARK_BG};")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        title_bar = QHBoxLayout()
        title = QLabel("APK Installer")
        title.setStyleSheet(f"color: {NEXOS_TEXT}; font-size: 16px; font-weight: bold; background: transparent;")
        title_bar.addWidget(title)
        title_bar.addStretch()

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {NEXOS_TEXT_DIM};
                border: none; font-size: 14px; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {NEXOS_ERROR}; color: white; }}
        """)
        close_btn.clicked.connect(self.close)
        title_bar.addWidget(close_btn)
        layout.addLayout(title_bar)

        layout.addSpacing(4)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(64, 64)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet(f"""
            QLabel {{
                background-color: {NEXOS_BORDER};
                border-radius: 12px;
            }}
        """)
        layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.name_label = QLabel(self.apk_path.stem)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet(f"color: {NEXOS_TEXT}; font-size: 15px; font-weight: bold; background: transparent;")
        layout.addWidget(self.name_label)

        self.package_label = QLabel("")
        self.package_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.package_label.setStyleSheet(f"color: {NEXOS_TEXT_DIM}; font-size: 11px; background: transparent;")
        layout.addWidget(self.package_label)

        layout.addSpacing(8)

        info_frame = NexusFrame()
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(6)

        self.status_label = QLabel("Ready to install")
        self.status_label.setStyleSheet(f"color: {NEXOS_TEXT}; font-size: 12px; background: transparent;")
        info_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {NEXOS_BORDER};
                border: none;
                border-radius: 4px;
                text-align: center;
                color: transparent;
            }}
            QProgressBar::chunk {{
                background-color: {NEXOS_ACCENT};
                border-radius: 4px;
            }}
        """)
        info_layout.addWidget(self.progress_bar)

        layout.addWidget(info_frame)
        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.cancel_btn = NexusButton("Cancel")
        self.cancel_btn.clicked.connect(self.handle_cancel)
        btn_layout.addWidget(self.cancel_btn)

        self.install_btn = NexusButton("Install", accent=True)
        self.install_btn.clicked.connect(self.start_install)
        btn_layout.addWidget(self.install_btn)

        layout.addLayout(btn_layout)

        self._center_window()

    def _center_window(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    def start_install(self):
        self.install_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setValue(0)

        self.worker = InstallWorker(self.apk_path)
        self.worker.progress.connect(self._on_progress)
        self.worker.status.connect(self._on_status)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.icon_ready.connect(self._on_icon)
        self.worker.package_info.connect(self._on_package_info)
        self.worker.start()

    def handle_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(3000)
            logger.info("Install cancelled by user: %s", self.apk_path.name)
        self.close()

    def _on_progress(self, value):
        self.progress_bar.setValue(value)

    def _on_status(self, message):
        self.status_label.setText(message)

    def _on_icon(self, pixmap):
        scaled = pixmap.scaled(
            64, 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.icon_label.setPixmap(scaled)
        self.icon_label.setStyleSheet(f"""
            QLabel {{
                border-radius: 12px;
                background: transparent;
            }}
        """)

    def _on_package_info(self, info):
        self.package_label.setText(info)

    def _on_finished(self, success, message):
        if success:
            self.status_label.setText(message)
            self.status_label.setStyleSheet(
                f"color: {NEXOS_SUCCESS}; font-size: 12px; font-weight: bold; background: transparent;"
            )
            self.progress_bar.setValue(100)
            QTimer.singleShot(2000, self.close)
        else:
            self.status_label.setText(message)
            self.status_label.setStyleSheet(
                f"color: {NEXOS_ERROR}; font-size: 12px; font-weight: bold; background: transparent;"
            )
            self.install_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)


class InstallWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    error = pyqtSignal(str)
    icon_ready = pyqtSignal(QPixmap)
    package_info = pyqtSignal(str)

    def __init__(self, apk_path: Path):
        super().__init__()
        self.apk_path = apk_path

    def run(self):
        try:
            self._do_install()
        except Exception as e:
            logger.exception("Installation failed")
            self.error.emit(str(e))
            self.finished.emit(False, f"Installation failed: {e}")

    def _do_install(self):
        if not self.apk_path.exists():
            self.finished.emit(False, "APK file not found")
            return

        self.status.emit("Validating APK file...")
        self.progress.emit(5)

        if not self._validate_apk():
            self.finished.emit(False, "Invalid APK file")
            return
        self.progress.emit(15)

        self.status.emit("Checking Waydroid...")
        if not self._check_waydroid():
            self.finished.emit(False, "Waydroid is not running. Start it first.")
            return
        self.progress.emit(20)

        self.status.emit("Extracting APK metadata...")
        app_name, package_name, version = self._extract_metadata()
        self.package_info.emit(f"{package_name} v{version}" if version else package_name)
        self.progress.emit(30)

        self.status.emit("Extracting app icon...")
        icon_path = self._extract_icon(package_name)
        if icon_path and icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                self.icon_ready.emit(pixmap)
        self.progress.emit(40)

        self.status.emit("Installing APK in Waydroid...")
        success = self._install_apk_waydroid()
        if not success:
            self.finished.emit(False, "Failed to install APK in Waydroid")
            return
        self.progress.emit(70)

        self.status.emit("Generating launcher entry...")
        self._generate_desktop_file(app_name or package_name, package_name, icon_path)
        self.progress.emit(85)

        self.status.emit("Updating game grid...")
        self._update_game_grid(app_name or package_name, package_name, icon_path)
        self.progress.emit(95)

        self.status.emit("Installation complete!")
        self.progress.emit(100)
        self.finished.emit(True, f"{app_name or package_name} installed successfully")

        logger.info("Successfully installed: %s (%s)", app_name, package_name)

    def _validate_apk(self) -> bool:
        try:
            with open(self.apk_path, "rb") as f:
                magic = f.read(4)
            if magic != APK_MAGIC:
                logger.error("Invalid APK magic bytes: %s", magic.hex())
                return False

            apk_size = self.apk_path.stat().st_size
            if apk_size < 1024:
                logger.error("APK too small: %d bytes", apk_size)
                return False

            if apk_size > 500 * 1024 * 1024:
                logger.warning("APK is very large: %d bytes", apk_size)

            with zipfile.ZipFile(self.apk_path, "r") as zf:
                namelist = zf.namelist()
                if "AndroidManifest.xml" not in namelist:
                    logger.error("Missing AndroidManifest.xml")
                    return False
                if "classes.dex" not in namelist and "classes2.dex" not in namelist:
                    logger.warning("No classes.dex found, APK may be invalid")

            logger.info("APK validation passed: %s (%d bytes)", self.apk_path.name, apk_size)
            return True
        except zipfile.BadZipFile:
            logger.error("File is not a valid ZIP/APK")
            return False
        except Exception as e:
            logger.error("Validation error: %s", e)
            return False

    def _check_waydroid(self) -> bool:
        try:
            result = subprocess.run(
                ["waydroid", "state", "info"],
                capture_output=True, text=True, timeout=10,
            )
            return "RUNNING" in result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _extract_metadata(self) -> tuple:
        app_name = ""
        package_name = ""
        version = ""

        try:
            result = subprocess.run(
                ["aapt", "dump", "badging", str(self.apk_path)],
                capture_output=True, text=True, timeout=30,
            )
            for line in result.stdout.splitlines():
                if line.startswith("package:"):
                    parts = line.split("'")
                    if len(parts) >= 4:
                        package_name = parts[1]
                        version = parts[3]
                elif line.startswith("application-label:"):
                    app_name = line.split("'")[1] if "'" in line else ""
        except FileNotFoundError:
            logger.warning("aapt not found, falling back to zip extraction")
        except subprocess.TimeoutExpired:
            logger.warning("aapt timed out")

        if not package_name:
            try:
                with zipfile.ZipFile(self.apk_path, "r") as zf:
                    if "AndroidManifest.xml" in zf.namelist():
                        raw = zf.read("AndroidManifest.xml")
                        import re
                        matches = re.findall(
                            rb"package[\x00-\xff]{0,20}?([a-zA-Z][a-zA-Z0-9_.]+)",
                            raw,
                        )
                        if matches:
                            package_name = matches[0].decode("utf-8", errors="ignore")
            except Exception:
                pass

        if not package_name:
            package_name = self.apk_path.stem

        if not app_name:
            app_name = package_name.rsplit(".", 1)[-1] if "." in package_name else package_name

        logger.info("Metadata: name=%s, pkg=%s, ver=%s", app_name, package_name, version)
        return app_name, package_name, version

    def _extract_icon(self, package_name: str) -> Path | None:
        NEXUS_ICONS_DIR.mkdir(parents=True, exist_ok=True)
        icon_dest = NEXUS_ICONS_DIR / f"{package_name}.png"

        try:
            with zipfile.ZipFile(self.apk_path, "r") as zf:
                icon_candidates = [
                    "res/mipmap-xxxhdpi/ic_launcher.png",
                    "res/mipmap-xxhdpi/ic_launcher.png",
                    "res/mipmap-xhdpi/ic_launcher.png",
                    "res/drawable-xxxhdpi/ic_launcher.png",
                    "res/drawable-xxhdpi/ic_launcher.png",
                    "res/mipmap-hdpi/ic_launcher.png",
                    "res/drawable-hdpi/ic_launcher.png",
                    "res/mipmap-mdpi/ic_launcher.png",
                    "res/drawable-mdpi/ic_launcher.png",
                    "res/mipmap-anydpi-v26/ic_launcher.xml",
                    "res/mipmap-anydpi-v26/ic_launcher_round.xml",
                ]

                namelist = set(zf.namelist())

                for candidate in icon_candidates:
                    if candidate in namelist:
                        if candidate.endswith(".png"):
                            data = zf.read(candidate)
                            with open(icon_dest, "wb") as f:
                                f.write(data)
                            return icon_dest
                        elif candidate.endswith(".xml"):
                            continue

                for name in namelist:
                    if "ic_launcher" in name and name.endswith(".png"):
                        data = zf.read(name)
                        with open(icon_dest, "wb") as f:
                            f.write(data)
                        return icon_dest

                for name in namelist:
                    if "icon" in name.lower() and name.endswith(".png"):
                        data = zf.read(name)
                        with open(icon_dest, "wb") as f:
                            f.write(data)
                        return icon_dest

        except Exception as e:
            logger.warning("Icon extraction failed: %s", e)

        return self._create_fallback_icon(package_name)

    def _create_fallback_icon(self, package_name: str) -> Path:
        NEXUS_ICONS_DIR.mkdir(parents=True, exist_ok=True)
        icon_dest = NEXUS_ICONS_DIR / f"{package_name}.png"

        img = QImage(128, 128, QImage.Format.Format_ARGB32)
        img.fill(QColor(NEXOS_ACCENT))

        img.save(str(icon_dest), "PNG")
        logger.info("Created fallback icon: %s", icon_dest)
        return icon_dest

    def _install_apk_waydroid(self) -> bool:
        self.status.emit("Pushing APK to Waydroid container...")
        self.progress.emit(45)

        try:
            with tempfile.TemporaryDirectory(prefix="nexusos_apk_") as tmpdir:
                tmp_apk = Path(tmpdir) / self.apk_path.name
                shutil.copy2(self.apk_path, tmp_apk)

                result = subprocess.run(
                    ["waydroid", "app", "install", str(tmp_apk)],
                    capture_output=True, text=True, timeout=120,
                )

                if result.returncode != 0:
                    logger.error("waydroid app install failed: %s", result.stderr)
                    if "not running" in result.stderr.lower():
                        self.status.emit("Error: Waydroid container not running")
                        return False
                    if "insufficient" in result.stderr.lower() or "no space" in result.stderr.lower():
                        self.status.emit("Error: Insufficient storage space")
                        return False
                    return False

                self.progress.emit(65)
                logger.info("APK pushed to Waydroid successfully")
                return True

        except subprocess.TimeoutExpired:
            logger.error("waydroid app install timed out")
            self.status.emit("Error: Installation timed out")
            return False
        except Exception as e:
            logger.error("Installation error: %s", e)
            return False

    def _generate_desktop_file(self, app_name: str, package_name: str, icon_path: Path | None):
        APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = package_name.replace(".", "-").lower()
        desktop_file = APPLICATIONS_DIR / f"nexusos-{safe_name}.desktop"

        icon_spec = ""
        if icon_path and icon_path.exists():
            icon_spec = str(icon_path)
        else:
            icon_spec = "application-x-executable"

        content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={app_name}
Comment=NexusOS Android App
Exec=waydroid app launch {package_name}
Icon={icon_spec}
Terminal=false
Categories=Android;Game;
MimeType={APK_MIME}
StartupNotify=true
Keywords=android;waydroid;nexusos;
"""
        desktop_file.write_text(content, encoding="utf-8")
        desktop_file.chmod(0o755)

        logger.info("Desktop file created: %s", desktop_file)

    def _update_game_grid(self, app_name: str, package_name: str, icon_path: Path | None):
        GAME_GRID_PATH.parent.mkdir(parents=True, exist_ok=True)

        grid = {}
        if GAME_GRID_PATH.exists():
            try:
                grid = json.loads(GAME_GRID_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                grid = {}

        entries = grid.get("entries", [])
        if isinstance(entries, dict):
            entries = list(entries.values())

        for entry in entries:
            if isinstance(entry, dict) and entry.get("package_name") == package_name:
                entry["name"] = app_name
                entry["last_updated"] = datetime.now().isoformat()
                grid["entries"] = entries
                GAME_GRID_PATH.write_text(json.dumps(grid, indent=2), encoding="utf-8")
                logger.info("Updated existing game grid entry for %s", package_name)
                return

        new_entry = {
            "name": app_name,
            "package_name": package_name,
            "icon": str(icon_path) if icon_path and icon_path.exists() else "",
            "source": "apk-install",
            "installed_at": datetime.now().isoformat(),
            "launch_cmd": f"waydroid app launch {package_name}",
        }
        entries.append(new_entry)
        grid["entries"] = entries
        grid["last_modified"] = datetime.now().isoformat()

        GAME_GRID_PATH.write_text(json.dumps(grid, indent=2), encoding="utf-8")
        logger.info("Added game grid entry for %s", package_name)


def register_mime_handler():
    try:
        desktop_dir = Path.home() / ".local" / "share" / "mime" / "packages"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        mime_xml = desktop_dir / "nexusos-apk-handler.xml"

        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="{APK_MIME}">
    <comment>NexusOS Android Package</comment>
    <glob pattern="*.apk"/>
    <sub-class-of type="application/zip"/>
  </mime-type>
</mime-info>"""
        mime_xml.write_text(xml_content, encoding="utf-8")

        subprocess.run(
            ["update-mime-database", str(desktop_dir.parent)],
            capture_output=True, timeout=10,
        )

        subprocess.run(
            ["xdg-mime", "default", "nexusos-apk-handler.desktop", APK_MIME],
            capture_output=True, timeout=10,
        )

        logger.info("MIME handler registered for .apk files")
    except Exception as e:
        logger.warning("Failed to register MIME handler: %s", e)


def check_single_instance():
    lock_file = Path("/tmp/nexusos-apk-installer.lock")
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            os.kill(pid, 0)
            logger.warning("Another instance running (PID %d), exiting", pid)
            return False
        except (ProcessLookupError, ValueError):
            pass

    lock_file.write_text(str(os.getpid()))
    return True


def cleanup_lock():
    lock_file = Path("/tmp/nexusos-apk-installer.lock")
    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            if pid == os.getpid():
                lock_file.unlink()
        except (ValueError, IOError):
            pass


def main():
    if not check_single_instance():
        sys.exit(1)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("NexusOS APK Installer")
    app.setOrganizationName("NexusOS")

    dark_style = f"""
        QApplication {{
            background-color: {NEXOS_DARK_BG};
            color: {NEXOS_TEXT};
        }}
        QToolTip {{
            background-color: {NEXOS_PANEL_BG};
            color: {NEXOS_TEXT};
            border: 1px solid {NEXOS_BORDER};
            padding: 4px;
            border-radius: 4px;
        }}
    """
    app.setStyleSheet(dark_style)

    apk_path = None
    if len(sys.argv) > 1:
        apk_path = sys.argv[1]
    else:
        logger.error("No APK file specified")
        print(f"Usage: {sys.argv[0]} <path-to.apk>")
        sys.exit(1)

    apk_file = Path(apk_path).resolve()
    if not apk_file.exists():
        logger.error("APK file not found: %s", apk_path)
        sys.exit(1)

    if not apk_file.suffix.lower() == ".apk":
        logger.warning("File does not have .apk extension: %s", apk_path)

    logger.info("Opening APK: %s", apk_file)

    register_mime_handler()

    window = InstallWindow(str(apk_file))
    window.show()

    exit_code = app.exec()
    cleanup_lock()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
