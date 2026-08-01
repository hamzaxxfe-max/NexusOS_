#!/usr/bin/env python3
"""Aion Live Wallpaper Selector — Browse, preview, and apply live wallpapers."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

BG_PRIMARY = "#121212"
BG_SECONDARY = "#1A2238"
ACCENT = "#00D2FF"
ACCENT_HOVER = "#33DBFF"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B0B0B0"
PANEL_BORDER = "#2A3550"

CONFIG_PATH = Path("/etc/aion/live-wallpaper.json")
DEFAULT_CONFIG = {
    "wallpaper_dir": "/usr/share/aion/live-wallpapers",
    "user_wallpaper_dir": "~/Videos/Aion-Wallpapers",
    "current_wallpaper": "",
}

SUPPORTED_VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv", ".ogv"}
SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ALL_EXT = SUPPORTED_VIDEO_EXT | SUPPORTED_IMAGE_EXT


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                config.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_current_wallpaper(path):
    config = load_config()
    config["current_wallpaper"] = path
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except OSError:
        pass


def get_wallpaper_files(dirs):
    files = []
    for d in dirs:
        p = Path(os.path.expanduser(str(d)))
        if not p.exists():
            continue
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in ALL_EXT:
                files.append(f)
    return files


def generate_thumbnail(video_path, thumb_path, time_sec=2):
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(time_sec), "-i", str(video_path),
                "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "5",
                str(thumb_path),
            ],
            timeout=15, capture_output=True,
        )
        return thumb_path.exists()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


class ScaleManager:
    def __init__(self, screen=None):
        if screen is None:
            screen = QApplication.primaryScreen()
        geo = screen.geometry()
        self.raw_w = geo.width()
        self.raw_h = geo.height()
        self.dpi = screen.logicalDotsPerInch()
        self.sf = self.dpi / 96.0

    def s(self, base_px):
        return max(1, int(base_px * self.sf))

    def fs(self, base_pt):
        return max(6, int(base_pt * self.sf))

    @property
    def thumb_size(self):
        return self.s(200)

    @property
    def card_width(self):
        return self.s(220)

    @property
    def card_height(self):
        return self.s(240)


class ThumbnailLoader(QThread):
    loaded = pyqtSignal(str, str)

    def __init__(self, tasks, parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self._running = True

    def run(self):
        thumb_dir = Path(tempfile.gettempdir()) / "aion-wallpaper-thumbs"
        thumb_dir.mkdir(exist_ok=True)
        for filepath, category in self._tasks:
            if not self._running:
                break
            fname = Path(filepath).stem + "_thumb.jpg"
            thumb_path = thumb_dir / fname
            if not thumb_path.exists():
                ext = Path(filepath).suffix.lower()
                if ext in SUPPORTED_VIDEO_EXT:
                    generate_thumbnail(filepath, thumb_path)
                else:
                    pixmap = QPixmap(filepath)
                    if not pixmap.isNull():
                        pixmap = pixmap.scaled(
                            320, 180, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        pixmap.save(str(thumb_path), "JPEG", 85)
            if thumb_path.exists():
                self.loaded.emit(str(filepath), str(thumb_path))

    def stop(self):
        self._running = False
        self.wait(2000)


class WallpaperCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, filepath, thumb_path, scale, parent=None):
        super().__init__(parent)
        self._filepath = filepath
        self._scale = scale
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui(thumb_path)

    def _build_ui(self, thumb_path):
        s = self._scale
        self.setFixedSize(s.card_width, s.card_height)
        self.setStyleSheet(f"""
            WallpaperCard {{
                background-color: {BG_SECONDARY};
                border: 2px solid {PANEL_BORDER};
                border-radius: 8px;
            }}
            WallpaperCard:hover {{
                border-color: {ACCENT};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(s.s(8), s.s(8), s.s(8), s.s(8))
        layout.setSpacing(s.s(4))
        thumb_label = QLabel()
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setFixedSize(s.thumb_size, s.thumb_size)
        pixmap = QPixmap(thumb_path)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                s.thumb_size, s.thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            pixmap = QPixmap(s.thumb_size, s.thumb_size)
            pixmap.fill(QColor(BG_SECONDARY))
        thumb_label.setPixmap(pixmap)
        layout.addWidget(thumb_label)
        name = Path(self._filepath).name
        name_label = QLabel(name)
        name_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: {s.fs(9)}pt;")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(True)
        layout.addWidget(name_label)
        ext = Path(self._filepath).suffix.upper().lstrip(".")
        type_label = QLabel(ext)
        type_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {s.fs(7)}pt;")
        type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(type_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._filepath)
        super().mousePressEvent(event)

    def set_selected(self, selected):
        self._selected = selected
        border = ACCENT if selected else PANEL_BORDER
        self.setStyleSheet(f"""
            WallpaperCard {{
                background-color: {BG_SECONDARY};
                border: 2px solid {border};
                border-radius: 8px;
            }}
            WallpaperCard:hover {{
                border-color: {ACCENT};
            }}
        """)


class WallpaperSelector(QMainWindow):
    def __init__(self):
        super().__init__()
        self._config = load_config()
        self._scale = ScaleManager()
        self._all_files = []
        self._filtered_files = []
        self._current_category = "All"
        self._selected_path = self._config.get("current_wallpaper", "")
        self._cards = []
        self.setWindowTitle("Aion Live Wallpaper Selector")
        self.setMinimumSize(self._scale.s(900), self._scale.s(600))
        self._build_ui()
        self._load_wallpapers()

    def _build_ui(self):
        s = self._scale
        central = QWidget()
        central.setStyleSheet(f"background-color: {BG_PRIMARY};")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(s.s(16), s.s(12), s.s(16), s.s(12))
        main_layout.setSpacing(s.s(10))
        header = self._build_header()
        main_layout.addWidget(header)
        search = self._build_search_bar()
        main_layout.addWidget(search)
        tabs = self._build_tabs()
        main_layout.addWidget(tabs)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: {BG_PRIMARY};
                border: none;
            }}
            QScrollBar:vertical {{
                background-color: {BG_SECONDARY};
                width: {s.s(10)}px;
                border-radius: {s.s(5)}px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {PANEL_BORDER};
                border-radius: {s.s(4)}px;
                min-height: {s.s(30)}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {ACCENT};
            }}
        """)
        self._grid_widget = QWidget()
        self._grid_layout = QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(s.s(12))
        self._scroll_area.setWidget(self._grid_widget)
        main_layout.addWidget(self._scroll_area, 1)
        footer = self._build_footer()
        main_layout.addWidget(footer)

    def _build_header(self):
        s = self._scale
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_SECONDARY};
                border-radius: {s.s(8)}px;
                padding: {s.s(12)}px;
            }}
        """)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(s.s(16), s.s(8), s.s(16), s.s(8))
        title = QLabel("Live Wallpapers")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: {s.fs(16)}pt; font-weight: bold;")
        layout.addWidget(title)
        layout.addStretch()
        count = len(self._all_files)
        info = QLabel(f"{count} wallpapers found")
        info.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {s.fs(10)}pt;")
        layout.addWidget(info)
        self._info_label = info
        return header

    def _build_search_bar(self):
        s = self._scale
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_SECONDARY};
                border-radius: {s.s(6)}px;
            }}
        """)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(s.s(12), s.s(4), s.s(12), s.s(4))
        search_icon = QLabel("Search:")
        search_icon.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {s.fs(10)}pt;")
        layout.addWidget(search_icon)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter wallpapers...")
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_PRIMARY};
                color: {TEXT_PRIMARY};
                border: 1px solid {PANEL_BORDER};
                border-radius: {s.s(4)}px;
                padding: {s.s(6)}px {s.s(10)}px;
                font-size: {s.fs(10)}pt;
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
        """)
        self._search_input.textChanged.connect(self._on_search)
        layout.addWidget(self._search_input)
        return frame

    def _build_tabs(self):
        s = self._scale
        tabs = QTabBar()
        tabs.setStyleSheet(f"""
            QTabBar {{
                background-color: transparent;
            }}
            QTabBar::tab {{
                background-color: {BG_SECONDARY};
                color: {TEXT_SECONDARY};
                padding: {s.s(8)}px {s.s(20)}px;
                margin-right: {s.s(2)}px;
                border-top-left-radius: {s.s(6)}px;
                border-top-right-radius: {s.s(6)}px;
                font-size: {s.fs(10)}pt;
            }}
            QTabBar::tab:selected {{
                background-color: {BG_PRIMARY};
                color: {ACCENT};
                border-bottom: 2px solid {ACCENT};
            }}
            QTabBar::tab:hover {{
                color: {TEXT_PRIMARY};
            }}
        """)
        for cat in ["All", "Videos", "Images"]:
            tabs.addTab(cat)
        tabs.currentChanged.connect(self._on_tab_changed)
        self._tabs = tabs
        return tabs

    def _build_footer(self):
        s = self._scale
        footer = QFrame()
        footer.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_SECONDARY};
                border-radius: {s.s(8)}px;
            }}
        """)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(s.s(16), s.s(8), s.s(16), s.s(8))
        self._selected_label = QLabel("No wallpaper selected")
        self._selected_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {s.fs(10)}pt;")
        layout.addWidget(self._selected_label)
        layout.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PRIMARY};
                color: {TEXT_SECONDARY};
                border: 1px solid {PANEL_BORDER};
                border-radius: {s.s(4)}px;
                padding: {s.s(6)}px {s.s(16)}px;
                font-size: {s.fs(10)}pt;
            }}
            QPushButton:hover {{
                border-color: {ACCENT};
                color: {TEXT_PRIMARY};
            }}
        """)
        refresh_btn.clicked.connect(self._load_wallpapers)
        layout.addWidget(refresh_btn)
        apply_btn = QPushButton("Apply Wallpaper")
        apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT};
                color: {BG_PRIMARY};
                border: none;
                border-radius: {s.s(4)}px;
                padding: {s.s(8)}px {s.s(24)}px;
                font-size: {s.fs(10)}pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_HOVER};
            }}
        """)
        apply_btn.clicked.connect(self._apply_wallpaper)
        layout.addWidget(apply_btn)
        return footer

    def _load_wallpapers(self):
        self._all_files = get_wallpaper_files([
            self._config.get("wallpaper_dir", ""),
            self._config.get("user_wallpaper_dir", ""),
        ])
        self._filtered_files = list(self._all_files)
        self._info_label.setText(f"{len(self._all_files)} wallpapers found")
        self._populate_grid()
        self._generate_thumbnails()

    def _generate_thumbnails(self):
        tasks = [(str(f), "all") for f in self._all_files]
        self._thumb_loader = ThumbnailLoader(tasks)
        self._thumb_loader.loaded.connect(self._on_thumbnail_loaded)
        self._thumb_loader.start()

    def _on_thumbnail_loaded(self, filepath, thumb_path):
        for card in self._cards:
            if card._filepath == filepath:
                pixmap = QPixmap(thumb_path)
                if not pixmap.isNull():
                    s = self._scale
                    pixmap = pixmap.scaled(
                        s.thumb_size, s.thumb_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    children = card.findChildren(QLabel)
                    if children:
                        children[0].setPixmap(pixmap)
                break

    def _populate_grid(self):
        s = self._scale
        self._cards.clear()
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cols = max(1, (s.raw_w - s.s(80)) // s.card_width)
        for i, filepath in enumerate(self._filtered_files):
            row = i // cols
            col = i % cols
            card = WallpaperCard(filepath, "", s)
            card.clicked.connect(self._on_card_clicked)
            if str(filepath) == self._selected_path:
                card.set_selected(True)
            self._grid_layout.addWidget(card, row, col)
            self._cards.append(card)

    def _on_card_clicked(self, filepath):
        self._selected_path = filepath
        name = Path(filepath).name
        self._selected_label.setText(f"Selected: {name}")
        self._selected_label.setStyleSheet(f"color: {ACCENT}; font-size: {self._scale.fs(10)}pt;")
        for card in self._cards:
            card.set_selected(card._filepath == filepath)

    def _on_tab_changed(self, index):
        categories = ["All", "Videos", "Images"]
        self._current_category = categories[index]
        self._apply_filter()

    def _on_search(self, text):
        self._apply_filter()

    def _apply_filter(self):
        search = self._search_input.text().lower()
        self._filtered_files = []
        for f in self._all_files:
            ext = f.suffix.lower()
            if self._current_category == "Videos" and ext not in SUPPORTED_VIDEO_EXT:
                continue
            if self._current_category == "Images" and ext not in SUPPORTED_IMAGE_EXT:
                continue
            if search and search not in f.name.lower():
                continue
            self._filtered_files.append(f)
        self._info_label.setText(f"{len(self._filtered_files)} wallpapers")
        self._populate_grid()

    def _apply_wallpaper(self):
        if not self._selected_path:
            QMessageBox.information(self, "No Selection", "Please select a wallpaper first.")
            return
        save_current_wallpaper(self._selected_path)
        self._signal_daemon()
        QMessageBox.information(
            self, "Applied",
            f"Wallpaper set to:\n{Path(self._selected_path).name}\n\n"
            "The change will take effect on next wallpaper cycle or daemon restart."
        )

    def _signal_daemon(self):
        try:
            pid_file = Path("/tmp/aion-live-wallpaper.pid")
            if pid_file.exists():
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGHUP)
        except (ValueError, OSError, FileNotFoundError):
            pass


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Aion Live Wallpaper Selector")
    selector = WallpaperSelector()
    selector.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    import signal
    main()
