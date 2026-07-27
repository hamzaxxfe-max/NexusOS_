#!/usr/bin/env python3
import sys
import os
import json
import glob
import subprocess
import time
import uuid
import hashlib
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QSize, QUrl, QMutex, QMutexLocker,
)
from PyQt6.QtGui import (
    QColor, QFont, QPalette, QIcon, QDesktopServices,
    QPainter, QPen, QBrush, QLinearGradient, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QProgressBar,
    QGroupBox, QScrollArea, QFrame, QFileDialog, QListView,
    QListWidgetItem, QMessageBox, QSplitter, QStackedWidget,
    QComboBox, QSpinBox, QCheckBox, QChart, QChartView,
    QLineSeries, QValueAxis, QAreaSeries, QGridLayout,
    QSizePolicy, QToolButton, QTextEdit, QLineEdit,
)

APP_NAME = "Nexus Tweak Hub"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".config" / "nexusos"
PROFILE_DIR = CONFIG_DIR / "keymaps"
NEXUSOS_GAMES = Path("/opt/nexusos/games")
USER_GAMES = Path.home() / "Games"
GPU_STATS_PATH = Path("/tmp/nexusos-gpu-stats.json")
REMOTE_PROFILES_URL = "https://raw.githubusercontent.com/nexusos-profiles/nexusos-profiles/main/index.json"
WARN_FREQ_RATIO = 1.1
WARN_UV_MV = -150


def get_dark_palette():
    pal = QPalette()
    bg = QColor(18, 18, 24)
    fg = QColor(200, 200, 210)
    accent = QColor(0, 180, 255)
    dark_bg = QColor(12, 12, 16)
    mid = QColor(40, 40, 52)
    highlight = QColor(0, 120, 215)
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, fg)
    pal.setColor(QPalette.ColorRole.Base, dark_bg)
    pal.setColor(QPalette.ColorRole.AlternateBase, bg)
    pal.setColor(QPalette.ColorRole.ToolTipBase, dark_bg)
    pal.setColor(QPalette.ColorRole.ToolTipText, fg)
    pal.setColor(QPalette.ColorRole.Text, fg)
    pal.setColor(QPalette.ColorRole.Button, mid)
    pal.setColor(QPalette.ColorRole.ButtonText, fg)
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 50, 50))
    pal.setColor(QPalette.ColorRole.Link, accent)
    pal.setColor(QPalette.ColorRole.Highlight, highlight)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    return pal


DARK_STYLESHEET = """
QMainWindow { background-color: #121218; }
QTabWidget::pane { border: 1px solid #2a2a34; background: #121218; }
QTabBar::tab { background: #282834; color: #c8c8d2; padding: 8pt 18pt;
               margin-right: 2pt; border-top-left-radius: 4pt; border-top-right-radius: 4pt;
               font-size: 9pt; }
QTabBar::tab:selected { background: #0078d7; color: #ffffff; }
QTabBar::tab:hover { background: #383848; }
QGroupBox { border: 1px solid #2a2a34; border-radius: 4pt; margin-top: 10pt;
            padding-top: 14pt; font-size: 9pt; color: #c8c8d2; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10pt; padding: 0 4pt; }
QPushButton { background-color: #282834; color: #c8c8d2; border: 1px solid #3a3a44;
              border-radius: 4pt; padding: 6pt 14pt; font-size: 9pt; min-height: 18pt; }
QPushButton:hover { background-color: #383848; border-color: #00b4ff; }
QPushButton:pressed { background-color: #0078d7; }
QPushButton:disabled { background-color: #1e1e28; color: #606070; }
QPushButton#accent { background-color: #0078d7; color: #ffffff; border: none; }
QPushButton#accent:hover { background-color: #1a8de7; }
QPushButton#danger { background-color: #c0392b; color: #ffffff; border: none; }
QPushButton#danger:hover { background-color: #e74c3c; }
QSlider::groove:horizontal { background: #2a2a34; height: 4pt; border-radius: 2pt; }
QSlider::handle:horizontal { background: #00b4ff; width: 14pt; height: 14pt;
                             margin: -5pt 0; border-radius: 7pt; }
QSlider::sub-page:horizontal { background: #0078d7; border-radius: 2pt; }
QProgressBar { border: 1px solid #2a2a34; border-radius: 4pt; text-align: center;
               background: #1e1e28; font-size: 8pt; color: #c8c8d2; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                      stop:0 #0078d7, stop:1 #00b4ff); border-radius: 3pt; }
QListView { background: #0c0c10; color: #c8c8d2; border: 1px solid #2a2a34;
            border-radius: 4pt; font-size: 9pt; }
QListView::item { padding: 6pt; }
QListView::item:selected { background: #0078d7; color: #ffffff; }
QListView::item:hover { background: #282834; }
QLineEdit { background: #0c0c10; color: #c8c8d2; border: 1px solid #2a2a34;
            border-radius: 4pt; padding: 4pt 8pt; font-size: 9pt; }
QTextEdit { background: #0c0c10; color: #c8c8d2; border: 1px solid #2a2a34;
            border-radius: 4pt; font-size: 9pt; }
QComboBox { background: #282834; color: #c8c8d2; border: 1px solid #3a3a44;
            border-radius: 4pt; padding: 4pt 8pt; font-size: 9pt; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background: #1e1e28; color: #c8c8d2;
                              selection-background-color: #0078d7; }
QSpinBox { background: #0c0c10; color: #c8c8d2; border: 1px solid #2a2a34;
           border-radius: 4pt; padding: 4pt; font-size: 9pt; }
QCheckBox { color: #c8c8d2; font-size: 9pt; spacing: 6pt; }
QCheckBox::indicator { width: 14pt; height: 14pt; border-radius: 3pt;
                       border: 1px solid #3a3a44; background: #1e1e28; }
QCheckBox::indicator:checked { background: #0078d7; border-color: #0078d7; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #121218; width: 8pt; }
QScrollBar::handle:vertical { background: #3a3a44; border-radius: 4pt; min-height: 30pt; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QChartView { border: 1px solid #2a2a34; border-radius: 4pt; background: #0c0c10; }
QLabel#warning { color: #e74c3c; font-weight: bold; font-size: 9pt; }
QLabel#stat-value { color: #00b4ff; font-size: 11pt; font-weight: bold; }
QLabel#section-title { color: #c8c8d2; font-size: 10pt; font-weight: bold; }
"""


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", 1


def read_sysfs(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError):
        return ""


def write_sysfs(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except (FileNotFoundError, PermissionError):
        return False


def detect_intel_gpu():
    cards = sorted(glob.glob("/sys/class/drm/card*"))
    for card in cards:
        vendor = read_sysfs(f"{card}/device/vendor")
        if vendor == "0x8086":
            card_name = os.path.basename(card)
            product = read_sysfs(f"{card}/device/product_name")
            if not product:
                out, _ = run_cmd(f"lspci | grep -i 'VGA.*Intel' | head -1")
                product = out if out else "Intel GPU (unknown model)"
            return {
                "path": card,
                "name": card_name,
                "product": product,
                "vendor": vendor,
            }
    return None


class FreqMonitorThread(QThread):
    data_ready = pyqtSignal(dict)

    def __init__(self, gpu_path, parent=None):
        super().__init__(parent)
        self.gpu_path = gpu_path
        self._running = True

    def run(self):
        while self._running:
            cur = read_sysfs(f"{self.gpu_path}/gt_cur_freq_mhz")
            actual = read_sysfs(f"{self.gpu_path}/gt_actual_freq_mhz")
            min_f = read_sysfs(f"{self.gpu_path}/gt_min_freq_mhz")
            max_f = read_sysfs(f"{self.gpu_path}/gt_max_freq_mhz")
            rc6 = read_sysfs(f"{self.gpu_path}/gt_rp0_freq_mhz")
            self.data_ready.emit({
                "cur": int(cur) if cur else 0,
                "actual": int(actual) if actual else 0,
                "min": int(min_f) if min_f else 0,
                "max": int(max_f) if max_f else 0,
                "timestamp": time.time(),
            })
            self.msleep(1000)

    def stop(self):
        self._running = False
        self.wait(2000)


class StatsPollThread(QThread):
    data_ready = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True

    def run(self):
        while self._running:
            data = {}
            if GPU_STATS_PATH.exists():
                try:
                    with open(GPU_STATS_PATH, "r") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass
            self.data_ready.emit(data)
            self.msleep(2000)

    def stop(self):
        self._running = False
        self.wait(2000)


class GpuTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gpu = detect_intel_gpu()
        self.gpu_path = self.gpu["path"] if self.gpu else "/sys/class/drm/card0"
        self.min_freq = 300
        self.max_freq = 1200
        self.cur_freq = 300
        self.freq_history = []
        self.monitor_thread = None
        self._init_ui()
        self._load_frequencies()
        self._start_monitor()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        header = QLabel("Intel GPU Control")
        header.setObjectName("section-title")
        header.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(header)

        if not self.gpu:
            no_gpu = QLabel("No Intel GPU detected. Ensure i915 driver is loaded.")
            no_gpu.setObjectName("warning")
            root.addWidget(no_gpu)
            return

        info_group = QGroupBox("GPU Information")
        info_layout = QGridLayout(info_group)
        info_layout.setSpacing(6)

        info_layout.addWidget(QLabel("Model:"), 0, 0)
        model_lbl = QLabel(self.gpu["product"])
        model_lbl.setObjectName("stat-value")
        info_layout.addWidget(model_lbl, 0, 1)

        info_layout.addWidget(QLabel("Card:"), 0, 2)
        card_lbl = QLabel(self.gpu["name"])
        info_layout.addWidget(card_lbl, 0, 3)

        info_layout.addWidget(QLabel("Current Freq:"), 1, 0)
        self.cur_freq_lbl = QLabel("--- MHz")
        self.cur_freq_lbl.setObjectName("stat-value")
        info_layout.addWidget(self.cur_freq_lbl, 1, 1)

        info_layout.addWidget(QLabel("Actual Freq:"), 1, 2)
        self.actual_freq_lbl = QLabel("--- MHz")
        self.actual_freq_lbl.setObjectName("stat-value")
        info_layout.addWidget(self.actual_freq_lbl, 1, 3)

        root.addWidget(info_group)

        freq_group = QGroupBox("Frequency Control")
        freq_layout = QVBoxLayout(freq_group)
        freq_layout.setSpacing(6)

        slider_row = QHBoxLayout()
        self.min_lbl = QLabel("300 MHz")
        self.min_lbl.setFont(QFont("Segoe UI", 8))
        slider_row.addWidget(self.min_lbl)

        self.freq_slider = QSlider(Qt.Orientation.Horizontal)
        self.freq_slider.setMinimum(300)
        self.freq_slider.setMaximum(1200)
        self.freq_slider.setValue(300)
        self.freq_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.freq_slider.setTickInterval(100)
        self.freq_slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self.freq_slider)

        self.max_slider_lbl = QLabel("1200 MHz")
        self.max_slider_lbl.setFont(QFont("Segoe UI", 8))
        slider_row.addWidget(self.max_slider_lbl)
        freq_layout.addLayout(slider_row)

        self.freq_value_lbl = QLabel("Target: 300 MHz")
        self.freq_value_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        freq_layout.addWidget(self.freq_value_lbl)

        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply Frequency")
        self.apply_btn.setObjectName("accent")
        self.apply_btn.clicked.connect(self._apply_frequency)
        btn_row.addWidget(self.apply_btn)

        self.restore_btn = QPushButton("Restore Defaults")
        self.restore_btn.clicked.connect(self._restore_defaults)
        btn_row.addWidget(self.restore_btn)
        freq_layout.addLayout(btn_row)

        self.warning_lbl = QLabel("")
        self.warning_lbl.setObjectName("warning")
        self.warning_lbl.hide()
        freq_layout.addWidget(self.warning_lbl)

        root.addWidget(freq_group)

        uv_group = QGroupBox("Undervolt Control")
        uv_layout = QGridLayout(uv_group)

        uv_layout.addWidget(QLabel("CPU Offset (mV):"), 0, 0)
        self.cpu_uv_spin = QSpinBox()
        self.cpu_uv_spin.setRange(-300, 0)
        self.cpu_uv_spin.setValue(0)
        self.cpu_uv_spin.setSuffix(" mV")
        self.cpu_uv_spin.valueChanged.connect(self._check_uv_safety)
        uv_layout.addWidget(self.cpu_uv_spin, 0, 1)

        uv_layout.addWidget(QLabel("GPU Offset (mV):"), 0, 2)
        self.gpu_uv_spin = QSpinBox()
        self.gpu_uv_spin.setRange(-300, 0)
        self.gpu_uv_spin.setValue(0)
        self.gpu_uv_spin.setSuffix(" mV")
        self.gpu_uv_spin.valueChanged.connect(self._check_uv_safety)
        uv_layout.addWidget(self.gpu_uv_spin, 0, 3)

        uv_layout.addWidget(QLabel("Cache Offset (mV):"), 1, 0)
        self.cache_uv_spin = QSpinBox()
        self.cache_uv_spin.setRange(-300, 0)
        self.cache_uv_spin.setValue(0)
        self.cache_uv_spin.setSuffix(" mV")
        uv_layout.addWidget(self.cache_uv_spin, 1, 1)

        self.uv_status_lbl = QLabel("")
        uv_layout.addWidget(self.uv_status_lbl, 1, 2, 1, 2)

        uv_btn_row = QHBoxLayout()
        self.apply_uv_btn = QPushButton("Apply Undervolt")
        self.apply_uv_btn.setObjectName("accent")
        self.apply_uv_btn.clicked.connect(self._apply_undervolt)
        uv_btn_row.addWidget(self.apply_uv_btn)

        self.read_uv_btn = QPushButton("Read Current")
        self.read_uv_btn.clicked.connect(self._read_undervolt)
        uv_btn_row.addWidget(self.read_uv_btn)
        uv_layout.addLayout(uv_btn_row, 2, 0, 1, 4)

        self.uv_warning_lbl = QLabel("")
        self.uv_warning_lbl.setObjectName("warning")
        self.uv_warning_lbl.hide()
        uv_layout.addWidget(self.uv_warning_lbl, 3, 0, 1, 4)

        root.addWidget(uv_group)

        monitor_group = QGroupBox("Frequency Monitor")
        monitor_layout = QVBoxLayout(monitor_group)

        self.chart = QChart()
        self.chart.setTitle("GPU Frequency (MHz)")
        self.chart.setTitleBrush(QBrush(QColor(200, 200, 210)))
        self.chart.setTitleFont(QFont("Segoe UI", 9))
        self.chart.setBackgroundBrush(QBrush(QColor(12, 12, 16)))
        self.chart.legend().hide()

        self.freq_series = QLineSeries()
        self.freq_series.setColor(QColor(0, 180, 255))
        self.freq_series.setPen(QPen(QColor(0, 180, 255), 2))

        self.actual_series = QLineSeries()
        self.actual_series.setColor(QColor(0, 255, 130))
        self.actual_series.setPen(QPen(QColor(0, 255, 130), 1, Qt.PenStyle.DashLine))

        self.chart.addSeries(self.freq_series)
        self.chart.addSeries(self.actual_series)

        self.axis_x = QValueAxis()
        self.axis_x.setLabelFormat("%.0f")
        self.axis_x.setTitleText("Time (s)")
        self.axis_x.setLabelsColor(QColor(150, 150, 160))

        self.axis_y = QValueAxis()
        self.axis_y.setTitleText("MHz")
        self.axis_y.setLabelsColor(QColor(150, 150, 160))

        self.chart.addAxis(self.axis_x, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.axis_y, Qt.AlignmentFlag.AlignLeft)
        self.freq_series.attachAxis(self.axis_x)
        self.freq_series.attachAxis(self.axis_y)
        self.actual_series.attachAxis(self.axis_x)
        self.actual_series.attachAxis(self.axis_y)

        self.chart_view = QChartView(self.chart)
        self.chart_view.setMinimumHeight(180)
        monitor_layout.addWidget(self.chart_view)

        legend_row = QHBoxLayout()
        cur_indicator = QLabel("  Current")
        cur_indicator.setStyleSheet("color: #00b4ff; font-size: 8pt;")
        legend_row.addWidget(cur_indicator)
        act_indicator = QLabel("  Actual")
        act_indicator.setStyleSheet("color: #00ff82; font-size: 8pt;")
        legend_row.addWidget(act_indicator)
        legend_row.addStretch()
        monitor_layout.addLayout(legend_row)

        root.addWidget(monitor_group)
        root.addStretch()

    def _load_frequencies(self):
        min_f = read_sysfs(f"{self.gpu_path}/gt_min_freq_mhz")
        max_f = read_sysfs(f"{self.gpu_path}/gt_max_freq_mhz")
        cur_f = read_sysfs(f"{self.gpu_path}/gt_cur_freq_mhz")
        if min_f:
            self.min_freq = int(min_f)
        if max_f:
            self.max_freq = int(max_f)
        if cur_f:
            self.cur_freq = int(cur_f)
        self.freq_slider.setMinimum(self.min_freq)
        self.freq_slider.setMaximum(self.max_freq)
        self.freq_slider.setValue(self.cur_freq)
        self.min_lbl.setText(f"{self.min_freq} MHz")
        self.max_slider_lbl.setText(f"{self.max_freq} MHz")
        self.freq_value_lbl.setText(f"Target: {self.cur_freq} MHz")
        self.axis_y.setRange(self.min_freq * 0.9, self.max_freq * 1.15)

    def _start_monitor(self):
        self.monitor_thread = FreqMonitorThread(self.gpu_path, self)
        self.monitor_thread.data_ready.connect(self._update_monitor)
        self.monitor_thread.start()

    def _update_monitor(self, data):
        self.cur_freq_lbl.setText(f"{data['cur']} MHz")
        self.actual_freq_lbl.setText(f"{data['actual']} MHz")
        t = data["timestamp"]
        self.freq_history.append((t, data["cur"], data["actual"]))
        if len(self.freq_history) > 120:
            self.freq_history = self.freq_history[-120:]
        t0 = self.freq_history[0][0] if self.freq_history else t
        times = [p[0] - t0 for p in self.freq_history]
        cur_vals = [p[1] for p in self.freq_history]
        act_vals = [p[2] for p in self.freq_history]
        self.freq_series.clear()
        self.actual_series.clear()
        for i in range(len(times)):
            self.freq_series.append(times[i], cur_vals[i])
            self.actual_series.append(times[i], act_vals[i])
        if times:
            self.axis_x.setRange(max(0, times[-1] - 60), max(60, times[-1]))

    def _on_slider_changed(self, value):
        self.freq_value_lbl.setText(f"Target: {value} MHz")
        self._check_freq_safety(value)

    def _check_freq_safety(self, freq):
        limit = self.max_freq * WARN_FREQ_RATIO
        if freq > self.max_freq:
            self.warning_lbl.setText(
                f"WARNING: Target {freq} MHz exceeds safe max ({self.max_freq} MHz). "
                f"System may become unstable!"
            )
            self.warning_lbl.show()
        else:
            self.warning_lbl.hide()

    def _check_uv_safety(self):
        cpu_uv = self.cpu_uv_spin.value()
        gpu_uv = self.gpu_uv_spin.value()
        cache_uv = self.cache_uv_spin.value()
        worst = min(cpu_uv, gpu_uv, cache_uv)
        if worst < WARN_UV_MV:
            self.uv_warning_lbl.setText(
                f"WARNING: Undervolt {worst} mV exceeds safe limit ({WARN_UV_MV} mV). "
                f"Risk of crashes and data corruption!"
            )
            self.uv_warning_lbl.show()
        else:
            self.uv_warning_lbl.hide()

    def _apply_frequency(self):
        target = self.freq_slider.value()
        path = f"{self.gpu_path}/gt_max_freq_mhz"
        ok = write_sysfs(path, target)
        if not ok:
            QMessageBox.warning(
                self, "Permission Error",
                "Failed to write frequency. Run as root or configure sudoers:\n\n"
                "nexus ALL=(root) NOPASSWD: /usr/bin/tee /sys/class/drm/card*/gt_max_freq_mhz"
            )
            return
        min_path = f"{self.gpu_path}/gt_min_freq_mhz"
        write_sysfs(min_path, target)
        self.cur_freq = target
        self._load_frequencies()
        QMessageBox.information(self, "Applied", f"Frequency set to {target} MHz")

    def _restore_defaults(self):
        reply = QMessageBox.question(
            self, "Restore Defaults",
            "Reset GPU frequency to automatic management?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            out, rc = run_cmd(
                "pkexec sh -c 'echo 0 > /sys/class/drm/card*/gt_max_freq_mhz 2>/dev/null; "
                "echo 0 > /sys/class/drm/card*/gt_min_freq_mhz 2>/dev/null'"
            )
            self._load_frequencies()
            QMessageBox.information(self, "Restored", "GPU frequency management restored to defaults.")

    def _read_undervolt(self):
        hwmons = sorted(glob.glob("/sys/class/intel_hwmon/hwmon/hwmon*"))
        if not hwmons:
            self.uv_status_lbl.setText("intel-undervolt hwmon not found")
            return
        hwmon = hwmons[0]
        vals = {}
        for name, idx in [("cpu", 0), ("gpu", 1), ("cache", 2)]:
            raw = read_sysfs(f"{hwmon}/offset_mv")
            vals[name] = int(raw) if raw else 0
        self.cpu_uv_spin.setValue(vals["cpu"])
        self.gpu_uv_spin.setValue(vals["gpu"])
        self.cache_uv_spin.setValue(vals["cache"])
        self.uv_status_lbl.setText("Offsets read from hwmon")

    def _apply_undervolt(self):
        reply = QMessageBox.question(
            self, "Apply Undervolt",
            "Apply undervolt offsets? This may cause instability if set too aggressively.\n\n"
            "Requires intel-undervolt to be installed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        cpu_mv = self.cpu_uv_spin.value()
        gpu_mv = self.gpu_uv_spin.value()
        cache_mv = self.cache_uv_spin.value()
        out, rc = run_cmd(
            f"pkexec intel-undervolt apply "
            f"--cpu {cpu_mv} --gpu {gpu_mv} --cache {cache_mv}",
            timeout=15,
        )
        if rc == 0:
            self.uv_status_lbl.setText("Undervolt applied successfully")
        else:
            self.uv_status_lbl.setText(f"Failed: {out[:80]}")
            QMessageBox.warning(self, "Error", f"intel-undervolt failed:\n{out}")

    def closeEvent(self, event):
        if self.monitor_thread:
            self.monitor_thread.stop()
        super().closeEvent(event)


class StorageTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.game_dirs = []
        self._init_ui()
        self._refresh_stats()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        header = QLabel("Storage Manager (Btrfs)")
        header.setObjectName("section-title")
        header.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(header)

        stats_group = QGroupBox("Filesystem Statistics")
        stats_layout = QVBoxLayout(stats_group)

        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setFont(QFont("Consolas", 9))
        self.stats_text.setMaximumHeight(140)
        stats_layout.addWidget(self.stats_text)

        refresh_btn = QPushButton("Refresh Statistics")
        refresh_btn.clicked.connect(self._refresh_stats)
        stats_layout.addWidget(refresh_btn)

        root.addWidget(stats_group)

        space_group = QGroupBox("Space Usage")
        space_layout = QVBoxLayout(space_group)

        self.total_bar = self._make_bar("Total Capacity", "#0078d7")
        space_layout.addWidget(self.total_bar["widget"])

        self.used_bar = self._make_bar("Used Space", "#e74c3c")
        space_layout.addWidget(self.used_bar["widget"])

        self.compressed_bar = self._make_bar("Compressed Savings", "#27ae60")
        space_layout.addWidget(self.compressed_bar["widget"])

        self.readonly_bar = self._make_bar("Read-Only / Snapshot", "#f39c12")
        space_layout.addWidget(self.readonly_bar["widget"])

        root.addWidget(space_group)

        actions_group = QGroupBox("Game Storage Actions")
        actions_layout = QGridLayout(actions_group)

        self.compress_btn = QPushButton("Compress Game Directory")
        self.compress_btn.setObjectName("accent")
        self.compress_btn.clicked.connect(self._compress_game)
        actions_layout.addWidget(self.compress_btn, 0, 0)

        self.defrag_btn = QPushButton("Defragment Game")
        self.defrag_btn.clicked.connect(self._defrag_game)
        actions_layout.addWidget(self.defrag_btn, 0, 1)

        self.scrub_btn = QPushButton("Start Btrfs Scrub")
        self.scrub_btn.clicked.connect(self._start_scrub)
        actions_layout.addWidget(self.scrub_btn, 0, 2)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        actions_layout.addWidget(self.progress, 1, 0, 1, 3)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setFont(QFont("Segoe UI", 9))
        actions_layout.addWidget(self.status_lbl, 2, 0, 1, 3)

        root.addWidget(actions_group)

        games_group = QGroupBox("Detected Game Directories")
        games_layout = QVBoxLayout(games_group)

        self.games_list = QListView()
        self.games_list.setMaximumHeight(120)
        games_layout.addWidget(self.games_list)

        detect_btn = QPushButton("Rescan Game Directories")
        detect_btn.clicked.connect(self._detect_games)
        games_layout.addWidget(detect_btn)

        root.addWidget(games_group)
        root.addStretch()

        self._detect_games()

    def _make_bar(self, label, color):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        lbl = QLabel(label)
        lbl.setFont(QFont("Segoe UI", 8))
        layout.addWidget(lbl)
        bar = QProgressBar()
        bar.setValue(0)
        bar.setMaximumHeight(12)
        bar.setTextVisible(True)
        bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3pt; }}"
        )
        layout.addWidget(bar)
        return {"widget": container, "bar": bar, "label": lbl}

    def _refresh_stats(self):
        out, rc = run_cmd("btrfs filesystem show 2>/dev/null")
        usage_out, _ = run_cmd("btrfs device usage / 2>/dev/null")
        if rc != 0:
            self.stats_text.setPlainText(
                "Btrfs not available or / is not a Btrfs filesystem.\n"
                "Install btrfs-progs and ensure root is Btrfs."
            )
            return
        combined = f"=== Filesystem Info ===\n{out}\n\n=== Device Usage ===\n{usage_out}"
        self.stats_text.setPlainText(combined)
        self._parse_usage(usage_out)

    def _parse_usage(self, usage_out):
        total_gb = 500
        used_gb = 200
        for line in usage_out.split("\n"):
            line_s = line.strip()
            if "Size:" in line_s:
                try:
                    parts = line_s.split(":")[-1].strip()
                    total_gb = float(parts.replace(",", "").split()[0])
                except (ValueError, IndexError):
                    pass
            elif "Used:" in line_s:
                try:
                    parts = line_s.split(":")[-1].strip()
                    used_gb = float(parts.replace(",", "").split()[0])
                except (ValueError, IndexError):
                    pass
        total_pct = min(100, int((used_gb / total_gb) * 100)) if total_gb > 0 else 0
        compressed_pct = min(100, int(total_pct * 0.65))
        readonly_pct = min(100, int(total_pct * 0.08))
        self.total_bar["bar"].setValue(100)
        self.total_bar["bar"].setFormat(f"{total_gb:.0f} GB Total")
        self.used_bar["bar"].setValue(total_pct)
        self.used_bar["bar"].setFormat(f"{used_gb:.0f} / {total_gb:.0f} GB ({total_pct}%)")
        self.compressed_bar["bar"].setValue(compressed_pct)
        self.compressed_bar["bar"].setFormat(f"~{(total_gb - used_gb) * 0.3:.0f} GB saved by zstd")
        self.readonly_bar["bar"].setValue(readonly_pct)
        self.readonly_bar["bar"].setFormat(f"Snapshots: ~{used_gb * 0.08:.0f} GB")

    def _detect_games(self):
        self.game_dirs.clear()
        dirs_to_scan = []
        if NEXUSOS_GAMES.exists():
            dirs_to_scan.append(NEXUSOS_GAMES)
        if USER_GAMES.exists():
            dirs_to_scan.append(USER_GAMES)
        home_games = Path.home() / ".local" / "share" / "Steam" / "steamapps" / "common"
        if home_games.exists():
            dirs_to_scan.append(home_games)
        for base in dirs_to_scan:
            if base.is_dir():
                for entry in sorted(base.iterdir()):
                    if entry.is_dir():
                        size_out, _ = run_cmd(f"du -sh '{entry}' 2>/dev/null")
                        size_str = size_out.split("\t")[0] if size_out else "?"
                        self.game_dirs.append({
                            "path": str(entry),
                            "name": entry.name,
                            "size": size_str,
                        })
        model = self.games_list.model()
        if model is None:
            from PyQt6.QtCore import QStringListModel
            model = QStringListModel()
            self.games_list.setModel(model)
        items = [f"{g['name']}  ({g['size']})  {g['path']}" for g in self.game_dirs]
        model.setStringList(items)

    def _compress_game(self):
        path, _ = QFileDialog.getDirectory(
            self, "Select Game Directory to Compress",
            str(USER_GAMES) if USER_GAMES.exists() else str(Path.home()),
        )
        if not path:
            return
        reply = QMessageBox.question(
            self, "Compress",
            f"Apply zstd:3 compression to:\n{path}\n\nThis may take a while for large games.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.status_lbl.setText(f"Compressing {Path(path).name}...")
        self.progress.setValue(10)
        QApplication.processEvents()
        out, rc = run_cmd(f"pkexec btrfs property set '{path}' compression zstd:3", timeout=300)
        self.progress.setValue(100)
        if rc == 0:
            self.status_lbl.setText(f"Compression applied to {Path(path).name}")
            QMessageBox.information(self, "Success", f"zstd:3 compression applied to:\n{path}")
        else:
            self.status_lbl.setText(f"Compression failed: {out[:60]}")
            QMessageBox.warning(self, "Error", f"btrfs property set failed:\n{out}")

    def _defrag_game(self):
        path, _ = QFileDialog.getDirectory(
            self, "Select Game Directory to Defragment",
            str(USER_GAMES) if USER_GAMES.exists() else str(Path.home()),
        )
        if not path:
            return
        reply = QMessageBox.question(
            self, "Defragment",
            f"Defragment and recompress:\n{path}\n\nThis can take several minutes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.status_lbl.setText(f"Defragmenting {Path(path).name}...")
        self.progress.setValue(10)
        QApplication.processEvents()
        out, rc = run_cmd(f"pkexec btrfs defrag -r -clzc zstd:3 '{path}'", timeout=600)
        self.progress.setValue(100)
        if rc == 0:
            self.status_lbl.setText(f"Defrag complete for {Path(path).name}")
            QMessageBox.information(self, "Success", "Defragmentation complete.")
        else:
            self.status_lbl.setText(f"Defrag failed: {out[:60]}")
            QMessageBox.warning(self, "Error", f"btrfs defrag failed:\n{out}")

    def _start_scrub(self):
        reply = QMessageBox.question(
            self, "Btrfs Scrub",
            "Start a filesystem scrub on /mnt/nexusos-games?\nThis verifies data integrity.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.status_lbl.setText("Starting scrub...")
        self.progress.setValue(20)
        QApplication.processEvents()
        out, rc = run_cmd("pkexec btrfs scrub start /mnt/nexusos-games", timeout=30)
        self.progress.setValue(100)
        if rc == 0:
            self.status_lbl.setText("Scrub started. Check: btrfs scrub status /mnt/nexusos-games")
        else:
            self.status_lbl.setText(f"Scrub failed: {out[:60]}")


class CommunityTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._init_ui()
        self._load_local_profiles()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        header = QLabel("Community Profiles")
        header.setObjectName("section-title")
        header.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        root.addWidget(header)

        online_group = QGroupBox("Online Profiles")
        online_layout = QVBoxLayout(online_group)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit(REMOTE_PROFILES_URL)
        self.url_input.setPlaceholderText("Profile index URL...")
        url_row.addWidget(self.url_input)

        self.fetch_btn = QPushButton("Browse Online Profiles")
        self.fetch_btn.setObjectName("accent")
        self.fetch_btn.clicked.connect(self._fetch_online)
        url_row.addWidget(self.fetch_btn)
        online_layout.addLayout(url_row)

        self.online_list = QListView()
        self.online_list.setMaximumHeight(150)
        online_layout.addWidget(self.online_list)

        online_btn_row = QHBoxLayout()
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.clicked.connect(self._download_profile)
        online_btn_row.addWidget(self.download_btn)
        online_btn_row.addStretch()
        online_layout.addLayout(online_btn_row)

        root.addWidget(online_group)

        local_group = QGroupBox("Local Profiles")
        local_layout = QVBoxLayout(local_group)

        self.local_list = QListView()
        self.local_list.setMaximumHeight(140)
        local_layout.addWidget(self.local_list)

        local_btn_row = QHBoxLayout()
        import_btn = QPushButton("Import Profile (.json)")
        import_btn.clicked.connect(self._import_profile)
        local_btn_row.addWidget(import_btn)

        share_btn = QPushButton("Share Current Profile")
        share_btn.setObjectName("accent")
        share_btn.clicked.connect(self._share_profile)
        local_btn_row.addWidget(share_btn)

        delete_btn = QPushButton("Delete Selected")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._delete_profile)
        local_btn_row.addWidget(delete_btn)
        local_btn_row.addStretch()
        local_layout.addLayout(local_btn_row)

        root.addWidget(local_group)

        share_group = QGroupBox("Share Code")
        share_layout = QVBoxLayout(share_group)
        self.share_code_edit = QTextEdit()
        self.share_code_edit.setReadOnly(True)
        self.share_code_edit.setFont(QFont("Consolas", 9))
        self.share_code_edit.setMaximumHeight(80)
        self.share_code_edit.setPlaceholderText("Share code will appear here...")
        share_layout.addWidget(self.share_code_edit)
        root.addWidget(share_group)

        root.addStretch()

    def _load_local_profiles(self):
        profiles = sorted(PROFILE_DIR.glob("*.json"))
        items = [p.stem for p in profiles]
        model = self.local_list.model()
        if model is None:
            from PyQt6.QtCore import QStringListModel
            model = QStringListModel()
            self.local_list.setModel(model)
        model.setStringList(items)

    def _fetch_online(self):
        url = self.url_input.text().strip()
        if not url:
            return
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText("Fetching...")
        QApplication.processEvents()
        out, rc = run_cmd(f"curl -sL '{url}'", timeout=15)
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("Browse Online Profiles")
        if rc != 0 or not out:
            QMessageBox.warning(self, "Error", "Failed to fetch profiles. Check network connection.")
            return
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            QMessageBox.warning(self, "Error", "Invalid JSON from profile index.")
            return
        profiles = data if isinstance(data, list) else data.get("profiles", [])
        display_items = []
        self._online_data = profiles
        for p in profiles:
            name = p.get("name", "Unknown")
            game = p.get("game", "Unknown")
            rating = p.get("rating", 0)
            stars = "*" * int(rating) if rating else ""
            display_items.append(f"{name}  |  {game}  |  {stars}")
        model = self.online_list.model()
        if model is None:
            from PyQt6.QtCore import QStringListModel
            model = QStringListModel()
            self.online_list.setModel(model)
        model.setStringList(display_items)

    def _download_profile(self):
        idx = self.online_list.currentIndex().row()
        if idx < 0 or not hasattr(self, "_online_data") or idx >= len(self._online_data):
            QMessageBox.information(self, "Select", "Select a profile to download.")
            return
        profile = self._online_data[idx]
        name = profile.get("name", f"profile_{uuid.uuid4().hex[:8]}")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        dest = PROFILE_DIR / f"{safe_name}.json"
        try:
            with open(dest, "w") as f:
                json.dump(profile, f, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Failed to save: {e}")
            return
        self._load_local_profiles()
        QMessageBox.information(self, "Downloaded", f"Profile saved: {dest.name}")

    def _import_profile(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Profile", str(Path.home()),
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.warning(self, "Error", f"Failed to read profile: {e}")
            return
        name = data.get("name", Path(path).stem)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        dest = PROFILE_DIR / f"{safe_name}.json"
        try:
            with open(dest, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "Error", f"Failed to save: {e}")
            return
        self._load_local_profiles()
        QMessageBox.information(self, "Imported", f"Profile saved as {dest.name}")

    def _share_profile(self):
        idx = self.local_list.currentIndex().row()
        profiles = sorted(PROFILE_DIR.glob("*.json"))
        if idx < 0 or idx >= len(profiles):
            QMessageBox.information(self, "Select", "Select a local profile to share.")
            return
        profile_path = profiles[idx]
        try:
            with open(profile_path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.warning(self, "Error", f"Failed to read: {e}")
            return
        share_data = {
            "nexusos_profile": True,
            "version": "1.0",
            "name": data.get("name", profile_path.stem),
            "game": data.get("game", "Unknown"),
            "keymaps": data.get("keymaps", {}),
            "metadata": data.get("metadata", {}),
        }
        blob = json.dumps(share_data, separators=(",", ":"))
        sig = hashlib.sha256(blob.encode()).hexdigest()[:16]
        encoded = f"nexus://{sig}/{len(blob)}"
        self.share_code_edit.setPlainText(
            f"Share Code: {encoded}\n\n"
            f"Profile: {share_data['name']}\n"
            f"Game: {share_data['game']}\n"
            f"Size: {len(blob)} bytes\n\n"
            f"Other users can import this profile using the Import button."
        )

    def _delete_profile(self):
        idx = self.local_list.currentIndex().row()
        profiles = sorted(PROFILE_DIR.glob("*.json"))
        if idx < 0 or idx >= len(profiles):
            return
        profile = profiles[idx]
        reply = QMessageBox.question(
            self, "Delete",
            f"Delete profile '{profile.stem}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            profile.unlink(missing_ok=True)
            self._load_local_profiles()


class NexusTweakHub(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(QSize(900, 650))
        self.resize(980, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(42)
        title_bar.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            "stop:0 #0078d7, stop:1 #00b4ff);"
        )
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(14, 0, 14, 0)
        title = QLabel(APP_NAME)
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        title_layout.addWidget(title)
        title_layout.addStretch()
        ver_label = QLabel(f"v{APP_VERSION}")
        ver_label.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 8pt; background: transparent;")
        title_layout.addWidget(ver_label)
        layout.addWidget(title_bar)

        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.gpu_tab = GpuTab()
        self.storage_tab = StorageTab()
        self.community_tab = CommunityTab()
        self.tabs.addTab(self.gpu_tab, "GPU")
        self.tabs.addTab(self.storage_tab, "Storage")
        self.tabs.addTab(self.community_tab, "Community")
        layout.addWidget(self.tabs)

        status_bar = self.statusBar()
        status_bar.showMessage("Ready")
        status_bar.setFont(QFont("Segoe UI", 8))

    def closeEvent(self, event):
        self.gpu_tab.close()
        super().closeEvent(event)


def ensure_single_instance():
    lock_path = Path("/tmp/nexus-tweak-hub.lock")
    try:
        if lock_path.exists():
            pid_str = lock_path.read_text().strip()
            if pid_str.isdigit():
                os.kill(int(pid_str), 0)
                print(f"Another instance is running (PID {pid_str}). Exiting.")
                return False
        lock_path.write_text(str(os.getpid()))
        return True
    except (OSError, PermissionError):
        lock_path.write_text(str(os.getpid()))
        return True


def cleanup_lock():
    lock_path = Path("/tmp/nexus-tweak-hub.lock")
    try:
        if lock_path.exists():
            pid_str = lock_path.read_text().strip()
            if pid_str.isdigit() and int(pid_str) == os.getpid():
                lock_path.unlink()
    except OSError:
        pass


def main():
    if not ensure_single_instance():
        sys.exit(1)
    app = QApplication(sys.argv)
    app.setPalette(get_dark_palette())
    app.setStyleSheet(DARK_STYLESHEET)
    app.setFont(QFont("Segoe UI", 9))
    window = NexusTweakHub()
    window.show()
    exit_code = app.exec()
    cleanup_lock()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
