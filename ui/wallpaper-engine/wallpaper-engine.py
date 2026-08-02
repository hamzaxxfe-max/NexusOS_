#!/usr/bin/env python3
"""Aion Wallpaper & Accent Color Engine — System Tray Manager."""

import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageFilter, ImageQt
from PyQt6.QtCore import (
    QFileSystemWatcher,
    QMutex,
    QMutexLocker,
    QSize,
    Qt,
    QThread,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Aion Wallpaper Engine"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".config" / "aion"
CONFIG_FILE = CONFIG_DIR / "wallpaper-engine.json"
LOCK_FILE = CONFIG_DIR / "wallpaper-engine.lock"
THUMB_DIR = CONFIG_DIR / "thumbnails"

WALLPAPER_DIRS = [
    Path("/usr/share/aion/wallpapers"),
    Path.home() / "Pictures" / "Wallpapers",
]

ACCENT_PRESETS = [
    {"name": "Electric Cyan", "color": "#00D2FF"},
    {"name": "Neon Violet", "color": "#A855F7"},
    {"name": "Plasma Green", "color": "#22D3EE"},
    {"name": "Solar Amber", "color": "#F59E0B"},
    {"name": "Hot Magenta", "color": "#EC4899"},
    {"name": "Frost White", "color": "#E2E8F0"},
]

SUPPORTED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("wallpaper-engine")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def run_cmd(cmd: list[str], timeout: int = 10) -> bool:
    try:
        subprocess.run(cmd, timeout=timeout, check=True,
                        capture_output=True, text=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("Command failed: %s — %s", " ".join(cmd), exc)
        return False


def get_desktop_env() -> str:
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in de or "plasma" in de:
        return "kde"
    if "gnome" in de or "unity" in de or "cinnamon" in de or "mate" in de:
        return "gnome"
    return "unknown"


def collect_wallpapers() -> list[Path]:
    wallpapers: list[Path] = []
    for d in WALLPAPER_DIRS:
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXT:
                    wallpapers.append(f)
    return wallpapers


def make_thumbnail(source: Path, size: int = 256) -> Path:
    thumb_path = THUMB_DIR / f"{source.stem}_{size}.png"
    if thumb_path.exists():
        src_mtime = source.stat().st_mtime
        thm_mtime = thumb_path.stat().st_mtime
        if thm_mtime >= src_mtime:
            return thumb_path
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(source)
    img.thumbnail((size, size), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(thumb_path, "PNG", optimize=True)
    return thumb_path


def pixmap_from_path(path: Path, size: int = 256) -> QPixmap:
    thumb = make_thumbnail(path, size)
    pixmap = QPixmap(str(thumb))
    if pixmap.isNull():
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor("#2A2A2A"))
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)


# ---------------------------------------------------------------------------
# Accent color application backends
# ---------------------------------------------------------------------------

def apply_plasma_wallpaper(path: str) -> bool:
    script = f"""
    var Desktops = desktops();
    for (var i = 0; i < Desktops.length; i++) {{
        Desktops[i].wallpaperPlugin = "org.kde.image";
        Desktops[i].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
        Desktops[i].writeConfig("Image", "file://{path}");
    }}
    """
    return run_cmd(["qdbus", "org.kde.plasmashell", "/PlasmaShell",
                     "org.kde.PlasmaShell.evaluateScript", script])


def apply_kwin_color_scheme(color_hex: str) -> bool:
    r, g, b = _hex_to_rgb(color_hex)
    run_cmd(["kwriteconfig6", "--file", "kdeglobals", "--group", "General",
             "--key", "AccentColor", f"{r},{g},{b}"])
    run_cmd(["kwriteconfig6", "--file", "kdeglobals", "--group", "WM",
             "--key", "activeBackground", f"{r},{g},{b}"])
    return run_cmd(["qdbus", "org.kde.KWin", "/KWin", "reconfigureConfiguration"])


def apply_xresources(color_hex: str) -> bool:
    r, g, b = _hex_to_rgb(color_hex)
    xresource = f"aion.accent_color: #{color_hex.lstrip('#')}\n"
    xresource += f"*color12: #{color_hex.lstrip('#')}\n"
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".Xresources",
                                          delete=False) as tmp:
            tmp.write(xresource)
            tmp_path = tmp.name
        run_cmd(["xrdb", "-merge", tmp_path])
        os.unlink(tmp_path)
        return True
    except OSError as exc:
        log.warning("Xresources apply failed: %s", exc)
        return False


def apply_gtk_color(color_hex: str) -> bool:
    r, g, b = _hex_to_rgb(color_hex)
    css_content = f"""@define-color accent_color rgb({r}, {g}, {b});
@define-color window_bg_color #121212;
@define-color window_fg_color #E2E8F0;
@define-color headerbar_bg_color #1A2238;
@define-color headerbar_fg_color #E2E8F0;
"""
    gtk_dirs = [
        Path.home() / ".config" / "gtk-4.0",
        Path.home() / ".config" / "gtk-3.0",
    ]
    for gtk_dir in gtk_dirs:
        gtk_dir.mkdir(parents=True, exist_ok=True)
        css_path = gtk_dir / "colors.css"
        css_path.write_text(css_content, encoding="utf-8")
        gtk_settings = gtk_dir / "settings.ini"
        section = "[Settings]\n"
        if gtk_settings.exists():
            existing = gtk_settings.read_text(encoding="utf-8")
            if "gtk-color-scheme" not in existing:
                section = existing + "\ngtk-color-scheme=accent_color=" + color_hex + "\n"
            else:
                section = ""
        if section:
            with open(gtk_settings, "a", encoding="utf-8") as f:
                f.write(section)
    return True


def apply_accent_all(color_hex: str) -> None:
    log.info("Applying accent color %s across desktop", color_hex)
    apply_kwin_color_scheme(color_hex)
    apply_xresources(color_hex)
    apply_gtk_color(color_hex)


def apply_wallpaper(path: str) -> None:
    log.info("Applying wallpaper: %s", path)
    de = get_desktop_env()
    if de == "kde":
        apply_plasma_wallpaper(path)
    elif de == "gnome":
        run_cmd(["gsettings", "set",
                  "org.gnome.desktop.background", "picture-uri",
                  f"file://{path}"])
        run_cmd(["gsettings", "set",
                  "org.gnome.desktop.background", "picture-uri-dark",
                  f"file://{path}"])
    else:
        run_cmd(["feh", "--bg-fill", path])


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ---------------------------------------------------------------------------
# Preview overlay
# ---------------------------------------------------------------------------

class PreviewOverlay(QWidget):
    """Fullscreen transparent overlay that shows the wallpaper for 5 seconds."""

    finished = pyqtSignal()

    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._pixmap = QPixmap(str(image_path))
        self._timer_label = QLabel("Closing in 5 s …", self)
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        self._timer_label.setStyleSheet(
            "color: white; font-size: 18px; font-weight: bold; "
            "background: rgba(0,0,0,160); padding: 8px 16px; border-radius: 6px;"
        )
        self._countdown = 5

    def start(self):
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        self.setGeometry(geo)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._tick()

    def _tick(self):
        if self._countdown <= 0:
            self.close()
            self.finished.emit()
            return
        self._timer_label.setText(f"Closing in {self._countdown} s …  (click to dismiss)")
        self._timer_label.adjustSize()
        self._timer_label.move(
            (self.width() - self._timer_label.width()) // 2,
            self.height() - self._timer_label.height() - 24,
        )
        self._countdown -= 1
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1000, self._tick)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillRect(self.rect(), QColor("#121212"))
        painter.end()

    def mousePressEvent(self, event):
        self.close()
        self.finished.emit()

    def keyPressEvent(self, event):
        self.close()
        self.finished.emit()


# ---------------------------------------------------------------------------
# Wallpaper thumbnail widget
# ---------------------------------------------------------------------------

class WallpaperCard(QFrame):
    clicked = pyqtSignal(Path)

    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        self.setFixedSize(170, 140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "WallpaperCard { background: #1E1E2E; border: 2px solid #2A2A3E; "
            "border-radius: 8px; }"
            "WallpaperCard:hover { border-color: #00D2FF; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(150, 95)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = pixmap_from_path(image_path, 150)
        self._thumb_label.setPixmap(pixmap)
        layout.addWidget(self._thumb_label, alignment=Qt.AlignmentFlag.AlignCenter)

        name = image_path.name
        if len(name) > 22:
            name = name[:19] + "…"
        self._name_label = QLabel(name)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setStyleSheet("color: #CCCCCC; font-size: 11px; border: none;")
        layout.addWidget(self._name_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.image_path)


# ---------------------------------------------------------------------------
# Color swatch button
# ---------------------------------------------------------------------------

class ColorSwatch(QPushButton):
    def __init__(self, color_hex: str, name: str, parent=None):
        super().__init__(parent)
        self.color_hex = color_hex
        self.color_name = name
        self.setFixedSize(48, 48)
        self.setToolTip(name)
        r, g, b = _hex_to_rgb(color_hex)
        self.setStyleSheet(
            f"QPushButton {{ background: {color_hex}; border: 3px solid #2A2A3E; "
            f"border-radius: 24px; }}"
            f"QPushButton:hover {{ border-color: #FFFFFF; }}"
            f"QPushButton:pressed {{ border-color: {color_hex}; }}"
        )


# ---------------------------------------------------------------------------
# Configuration persistence
# ---------------------------------------------------------------------------

class Config:
    def __init__(self):
        self.data: dict = {
            "accent_color": "#00D2FF",
            "last_wallpaper": "",
            "custom_wallpapers": [],
            "thumbnail_size": 256,
        }
        self._load()

    def _load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.data.update(loaded)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Config load error: %s", exc)

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError as exc:
            log.error("Config save error: %s", exc)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value
        self.save()


# ---------------------------------------------------------------------------
# Thumbnail worker thread
# ---------------------------------------------------------------------------

class ThumbnailWorker(QThread):
    batch_ready = pyqtSignal(list)

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.paths = paths
        self._abort = False

    def run(self):
        batch: list[tuple[Path, Path]] = []
        for p in self.paths:
            if self._abort:
                return
            try:
                thumb = make_thumbnail(p, 150)
                batch.append((p, thumb))
                if len(batch) >= 12:
                    self.batch_ready.emit(batch)
                    batch = []
            except Exception as exc:
                log.warning("Thumbnail generation failed for %s: %s", p, exc)
        if batch:
            self.batch_ready.emit(batch)

    def abort(self):
        self._abort = True


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class WallpaperEngineWindow(QMainWindow):
    def __init__(self, config: Config, tray: QSystemTrayIcon):
        super().__init__()
        self.config = config
        self.tray = tray
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(920, 620)
        self.resize(1020, 680)
        self._selected_wallpaper: Path | None = None
        self._worker: ThumbnailWorker | None = None
        self._preview_overlay: PreviewOverlay | None = None
        self._watcher = QFileSystemWatcher()

        self._apply_nexus_theme()
        self._build_ui()
        self._load_wallpapers()

        for d in WALLPAPER_DIRS:
            if d.is_dir():
                self._watcher.addPath(str(d))

    def _apply_nexus_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#121212"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#E2E8F0"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#1A1A2E"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1E1E2E"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1A2238"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#E2E8F0"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#E2E8F0"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#1A2238"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#E2E8F0"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#00D2FF"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#00D2FF"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
        QApplication.instance().setPalette(palette)
        self.setStyleSheet("""
            QMainWindow { background: #121212; }
            QTabWidget::pane { border: 1px solid #2A2A3E; background: #121212; border-radius: 6px; }
            QTabBar::tab { background: #1A2238; color: #CCCCCC; padding: 8px 18px;
                           border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
            QTabBar::tab:selected { background: #00D2FF; color: #000000; font-weight: bold; }
            QTabBar::tab:hover { background: #243352; }
            QPushButton { background: #1A2238; color: #E2E8F0; border: 1px solid #2A2A3E;
                           border-radius: 6px; padding: 8px 16px; font-size: 13px; }
            QPushButton:hover { border-color: #00D2FF; background: #243352; }
            QPushButton:pressed { background: #00D2FF; color: #000000; }
            QPushButton:disabled { background: #1E1E2E; color: #555555; border-color: #2A2A3E; }
            QLineEdit { background: #1A1A2E; color: #E2E8F0; border: 1px solid #2A2A3E;
                         border-radius: 6px; padding: 6px 10px; font-size: 13px; }
            QLineEdit:focus { border-color: #00D2FF; }
            QLabel { color: #E2E8F0; }
            QScrollArea { border: none; background: #121212; }
            QScrollBar:vertical { background: #121212; width: 10px; }
            QScrollBar::handle:vertical { background: #2A2A3E; border-radius: 5px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #00D2FF; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QSlider::groove:horizontal { background: #2A2A3E; height: 6px; border-radius: 3px; }
            QSlider::handle:horizontal { background: #00D2FF; width: 16px; height: 16px;
                                         margin: -5px 0; border-radius: 8px; }
            QSlider::handle:horizontal:hover { background: #33DDFF; }
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        header = QLabel(f"<span style='font-size:22px;font-weight:bold;color:#00D2FF;'>"
                        f"Aion Wallpaper Engine</span>"
                        f"<span style='font-size:12px;color:#888888;'> v{APP_VERSION}</span>")
        main_layout.addWidget(header)

        tabs = QTabWidget()
        main_layout.addWidget(tabs)

        tabs.addTab(self._build_wallpaper_tab(), "Wallpapers")
        tabs.addTab(self._build_accent_tab(), "Accent Colors")
        tabs.addTab(self._build_settings_tab(), "Settings")

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self._btn_apply = QPushButton("Apply Wallpaper")
        self._btn_apply.setMinimumHeight(38)
        self._btn_apply.clicked.connect(self._on_apply_wallpaper)
        btn_bar.addWidget(self._btn_apply)

        self._btn_apply_accent = QPushButton("Apply Accent")
        self._btn_apply_accent.setMinimumHeight(38)
        self._btn_apply_accent.clicked.connect(self._on_apply_accent)
        btn_bar.addWidget(self._btn_apply_accent)

        self._btn_preview = QPushButton("Preview (5 s)")
        self._btn_preview.setMinimumHeight(38)
        self._btn_preview.clicked.connect(self._on_preview)
        btn_bar.addWidget(self._btn_preview)

        btn_bar.addStretch()

        self._btn_reset = QPushButton("Reset Defaults")
        self._btn_reset.setMinimumHeight(38)
        self._btn_reset.clicked.connect(self._on_reset)
        btn_bar.addWidget(self._btn_reset)

        main_layout.addLayout(btn_bar)

    def _build_wallpaper_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search wallpapers…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_wallpapers)
        search_row.addWidget(self._search)

        btn_add = QPushButton("+ Add Folder")
        btn_add.clicked.connect(self._add_custom_dir)
        search_row.addWidget(btn_add)
        layout.addLayout(search_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scroll.setWidget(self._grid_container)
        layout.addWidget(self._scroll)

        status_row = QHBoxLayout()
        self._status_label = QLabel("Loading…")
        self._status_label.setStyleSheet("color: #888888; font-size: 11px;")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        return tab

    def _build_accent_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        section = QLabel("Preset accent colors")
        section.setStyleSheet("font-size: 14px; font-weight: bold; color: #E2E8F0;")
        layout.addWidget(section)

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(12)
        self._swatch_buttons: list[ColorSwatch] = []
        for preset in ACCENT_PRESETS:
            sw = ColorSwatch(preset["color"], preset["name"])
            sw.clicked.connect(lambda checked, c=preset["color"]: self._pick_accent(c))
            swatch_row.addWidget(sw)
            self._swatch_buttons.append(sw)
        swatch_row.addStretch()
        layout.addLayout(swatch_row)

        self._current_color_frame = QFrame()
        self._current_color_frame.setFixedSize(64, 64)
        self._current_color_frame.setStyleSheet(
            f"background: {self.config.get('accent_color', '#00D2FF')}; "
            "border: 3px solid #2A2A3E; border-radius: 32px;"
        )
        layout.addWidget(self._current_color_frame, alignment=Qt.AlignmentFlag.AlignLeft)

        custom_row = QHBoxLayout()
        btn_custom = QPushButton("Custom Color…")
        btn_custom.clicked.connect(self._on_custom_color)
        custom_row.addWidget(btn_custom)
        custom_row.addStretch()
        layout.addLayout(custom_row)

        layout.addStretch()
        return tab

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Configuration file:"))
        path_label = QLabel(str(CONFIG_FILE))
        path_label.setStyleSheet("color: #00D2FF; font-size: 12px;")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)

        layout.addWidget(QLabel("Wallpaper directories:"))
        for d in WALLPAPER_DIRS:
            dl = QLabel(f"  {d}")
            dl.setStyleSheet("color: #AAAAAA; font-size: 12px;")
            dl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(dl)

        for cd in self.config.get("custom_wallpapers", []):
            dl = QLabel(f"  {cd}")
            dl.setStyleSheet("color: #AAAAAA; font-size: 12px;")
            dl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(dl)

        layout.addSpacing(10)
        layout.addWidget(QLabel("Desktop environment:"))
        de_label = QLabel(f"  {get_desktop_env().upper()}")
        de_label.setStyleSheet("color: #00D2FF; font-size: 12px; font-weight: bold;")
        layout.addWidget(de_label)

        layout.addStretch()
        return tab

    # -- wallpaper loading & filtering --

    def _load_wallpapers(self):
        all_paths = collect_wallpapers()
        for cd in self.config.get("custom_wallpapers", []):
            p = Path(cd)
            if p.is_dir():
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix.lower() in SUPPORTED_EXT:
                        if f not in all_paths:
                            all_paths.append(f)
        self._all_wallpapers = all_paths
        self._populate_grid(all_paths)

    def _populate_grid(self, paths: list[Path]):
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(2000)

        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self._status_label.setText(f"Loading {len(paths)} wallpapers…")
        if not paths:
            self._status_label.setText("No wallpapers found. Add a folder or place images in the directories above.")
            return

        self._cards: list[WallpaperCard] = []
        cols = max(1, (self._scroll.viewport().width() - 20) // 180)
        for idx, wp in enumerate(paths):
            row, col = divmod(idx, cols)
            card = WallpaperCard(wp)
            card.clicked.connect(self._on_card_clicked)
            self._grid_layout.addWidget(card, row, col)
            self._cards.append(card)

        self._worker = ThumbnailWorker(paths)
        self._worker.batch_ready.connect(self._on_thumbnails_ready)
        self._worker.start()

        self._status_label.setText(f"{len(paths)} wallpapers loaded")

    def _on_thumbnails_ready(self, batch: list[tuple[Path, Path]]):
        card_map = {c.image_path: c for c in self._cards}
        for wp, thumb_path in batch:
            card = card_map.get(wp)
            if card:
                pixmap = QPixmap(str(thumb_path))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        150, 95, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    card._thumb_label.setPixmap(scaled)

    def _filter_wallpapers(self, text: str):
        q = text.strip().lower()
        if not q:
            filtered = self._all_wallpapers
        else:
            filtered = [p for p in self._all_wallpapers if q in p.stem.lower()]
        self._populate_grid(filtered)

    def _add_custom_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select wallpaper directory")
        if d:
            custom = self.config.get("custom_wallpapers", [])
            if d not in custom:
                custom.append(d)
                self.config.set("custom_wallpapers", custom)
            self._load_wallpapers()

    def _on_card_clicked(self, path: Path):
        self._selected_wallpaper = path
        self._btn_apply.setEnabled(True)
        self._btn_preview.setEnabled(True)
        self._status_label.setText(f"Selected: {path.name}")
        for card in self._cards:
            if card.image_path == path:
                card.setStyleSheet(
                    "WallpaperCard { background: #1E1E2E; border: 2px solid #00D2FF; "
                    "border-radius: 8px; }"
                )
            else:
                card.setStyleSheet(
                    "WallpaperCard { background: #1E1E2E; border: 2px solid #2A2A3E; "
                    "border-radius: 8px; }"
                )

    # -- accent color --

    def _pick_accent(self, color_hex: str):
        self.config.set("accent_color", color_hex)
        self._current_color_frame.setStyleSheet(
            f"background: {color_hex}; border: 3px solid #2A2A3E; border-radius: 32px;"
        )
        self._status_label.setText(f"Accent color set to {color_hex}")

    def _on_custom_color(self):
        current = QColor(self.config.get("accent_color", "#00D2FF"))
        color = QColorDialog.getColor(current, self, "Select Accent Color",
                                       QColorDialog.ColorDialogOptions(QColorDialog.ColorDialogOption.ShowAlphaChannel))
        if color.isValid():
            self._pick_accent(color.name())

    # -- apply / preview / reset --

    def _on_apply_wallpaper(self):
        if not self._selected_wallpaper:
            QMessageBox.information(self, "No Selection", "Select a wallpaper first.")
            return
        apply_wallpaper(str(self._selected_wallpaper))
        self.config.set("last_wallpaper", str(self._selected_wallpaper))
        self.tray.showMessage(
            APP_NAME,
            f"Wallpaper applied: {self._selected_wallpaper.name}",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _on_apply_accent(self):
        color = self.config.get("accent_color", "#00D2FF")
        apply_accent_all(color)
        self.tray.showMessage(
            APP_NAME,
            f"Accent color applied: {color}",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _on_preview(self):
        if not self._selected_wallpaper:
            return
        self._preview_overlay = PreviewOverlay(self._selected_wallpaper)
        self._preview_overlay.finished.connect(self._on_preview_done)
        self._preview_overlay.start()

    def _on_preview_done(self):
        self._preview_overlay = None

    def _on_reset(self):
        reply = QMessageBox.question(
            self, "Reset Defaults",
            "Reset accent color to Electric Cyan and clear selection?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._pick_accent("#00D2FF")
            self._selected_wallpaper = None
            self._status_label.setText("Reset to defaults")
            for card in self._cards:
                card.setStyleSheet(
                    "WallpaperCard { background: #1E1E2E; border: 2px solid #2A2A3E; "
                    "border-radius: 8px; }"
                )

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()


# ---------------------------------------------------------------------------
# Single instance lock
# ---------------------------------------------------------------------------

class SingleInstanceLock:
    def __init__(self):
        self._fd = None

    def acquire(self) -> bool:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except OSError:
            self._fd.close()
            self._fd = None
            return False

    def release(self):
        if self._fd:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
                LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

def create_tray_icon(app: QApplication, window: WallpaperEngineWindow) -> QSystemTrayIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#00D2FF"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
    painter.setPen(QPen(QColor("#000000"), 3))
    painter.setFont(QFont("Arial", 28, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "N")
    painter.end()

    tray = QSystemTrayIcon(QIcon(pixmap), app)
    tray.setToolTip(APP_NAME)

    menu = tray.activated.connect(
        lambda reason: window.show_window()
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick
        else None
    )

    from PyQt6.QtWidgets import QMenu
    ctx = QMenu()
    show_action = ctx.addAction("Open Wallpaper Engine")
    show_action.triggered.connect(window.show_window)
    ctx.addSeparator()
    quit_action = ctx.addAction("Quit")
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(ctx)

    return tray


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    lock = SingleInstanceLock()
    if not lock.acquire():
        print(f"{APP_NAME} is already running. Check {LOCK_FILE}")
        sys.exit(1)

    signal.signal(signal.SIGTERM, lambda *_: QApplication.quit())

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)

    config = Config()
    window = WallpaperEngineWindow(config, None)
    tray = create_tray_icon(app, window)
    window.tray = tray
    tray.show()

    tray.showMessage(APP_NAME, "Running in system tray.", QSystemTrayIcon.MessageIcon.Information, 2000)

    exit_code = app.exec()
    lock.release()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
