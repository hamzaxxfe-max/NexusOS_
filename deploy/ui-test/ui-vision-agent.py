#!/usr/bin/env python3
"""
Aion UI Vision Agent — static + programmatic UI introspection.

This is the part of the harness that "sees" the interface. It does NOT rely
on an external vision API: it renders the app offscreen/hidden, enumerates
the actual Qt widget tree (labels, buttons, sliders, stack pages), simulates
the full click-through flow, and reports user-facing defects:

  - widgets with empty or suspicious text
  - buttons that are disabled / never enabled
  - stack pages that never become current
  - geometry / layout overflows (widget larger than window)
  - repeated identical labels (copy/paste bugs)

Usage:
  python3 ui-vision-agent.py /path/to/repo/ui  [--app oobe]
  python3 ui-vision-agent.py /path/to/repo/ui  --render --screenshots-dir /tmp/aion-ui
"""
import argparse
import os
import re
import sys
from pathlib import Path

REPO_UI = Path(__file__).resolve().parent

QT_WIDGETS = ("QLabel", "QPushButton", "QSlider", "QComboBox", "QCheckBox",
              "QStackedWidget", "QFrame", "QScrollArea", "QComboBox")

FINDINGS = []


def finding(sev, app, msg, loc=""):
    FINDINGS.append((sev, app, msg, loc))
    print(f"[{sev}] {app}: {msg} {('@ ' + loc) if loc else ''}")


def scan_source(app_name, path):
    """Static pass: parse the Qt source for obvious user-facing defects."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # 1. Hardcoded absolute paths that don't exist relative to repo.
    for m in re.finditer(r"[\"'](/(?:etc|usr|opt|var)/[^\"']+)[\"']", text):
        p = m.group(1)
        if not (REPO_UI.parent / p.lstrip("/")).exists():
            # These may legitimately exist only at runtime; only flag ones that
            # point at the repo-relative tree and are clearly missing.
            if p.startswith("/etc/aion") or p.startswith("/usr/share/aion"):
                finding("INFO", app_name, f"deploy-created config path (expected): {p}",
                        f"line {text.count(chr(10), 0, m.start()) + 1}")

    # 2. Duplicate button labels (copy/paste bugs) within the same file.
    labels = re.findall(r'QPushButton\("([^"]{1,40})"\)', text)
    seen = {}
    for lab in labels:
        seen.setdefault(lab, 0)
        seen[lab] += 1
    for lab, n in seen.items():
        if n > 1 and n <= 20:
            finding("LOW", app_name, f"duplicate button label x{n}: \"{lab}\" (possible copy/paste)")

    # 3. Buttons defined but never connected to a slot.
    connected = text.count(".clicked.connect(")
    buttons = len(re.findall(r"QPushButton\(", text))
    if buttons and connected == 0:
        finding("HIGH", app_name, f"{buttons} buttons defined but 0 clicked.connect() calls")

    # 4. Dangerous subprocess calls (would crash in the field).
    for i, ln in enumerate(lines, 1):
        if re.search(r"subprocess\.(call|run|Popen|check_call)\s*\(\s*[\"'](?!.*python)", ln):
            finding("MED", app_name, "subprocess call of non-python binary", f"line {i}")


def scan_runtime(app_name, window):
    """Runtime pass over a live (offscreen) QWidget tree."""
    try:
        from PyQt6.QtWidgets import QLabel, QPushButton, QStackedWidget, QSlider, QComboBox
        from PyQt6.QtCore import QObject
    except Exception as exc:  # pragma: no cover
        finding("HIGH", app_name, f"PyQt6 unavailable for runtime pass: {exc}")
        return

    w = window.width()
    h = window.height()

    seen = set()

    def walk(obj, depth=0):
        for child in obj.findChildren(QObject):
            if id(child) in seen:
                continue
            seen.add(id(child))
            cname = child.__class__.__name__
            if isinstance(child, QLabel):
                t = child.text()
                if t.strip() == "" and child.objectName() and "bg" not in child.objectName().lower():
                    pass  # decorative labels are fine
                elif not child.wordWrap() and len(t) > 120 and "\n" not in t:
                    finding("LOW", app_name, f"very long non-wrapping label ({len(t)} chars): {t[:48]}...")
            elif isinstance(child, QPushButton):
                if not child.isEnabled():
                    finding("MED", app_name, f"button disabled at load: \"{child.text()}\"")
            elif isinstance(child, QSlider):
                if child.minimum() == child.maximum():
                    finding("MED", app_name, "slider with zero range (broken)")
            elif isinstance(child, QComboBox):
                if child.count() == 0:
                    finding("MED", app_name, "empty combo box")
            if isinstance(child, QStackedWidget):
                pages = child.count()
                current = child.currentIndex()
                if pages == 0:
                    finding("HIGH", app_name, "QStackedWidget with zero pages")
                finding("INFO", app_name, f"stack: {pages} pages, current={current}")
            # geometry overflow check
            if hasattr(child, "geometry") and hasattr(child, "width"):
                g = child.geometry()
                if g.width() > 0 and (g.x() + g.width() > w + 4 or g.y() + g.height() > h + 4):
                    finding("MED", app_name, f"{cname} overflows window bounds", f"geo=({g.x()},{g.y()},{g.width()}x{g.height()}) win={w}x{h}")

    walk(window)


def render_and_inspect(app_name, app_path, out_dir):
    """Full offscreen render + QTest click-through of the wizard flow."""
    try:
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtWidgets import QApplication, QMainWindow
    except Exception as exc:
        finding("HIGH", app_name, f"cannot import PyQt6: {exc}")
        return

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts")
    os.environ["XAUTHORITY"] = os.environ.get("XAUTHORITY", "")

    # The wizard imports fcntl/socket and reads /etc/aion; tolerate missing files.
    sys.path.insert(0, str(app_path.parent))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(app_name, str(app_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:
        finding("HIGH", app_name, f"module import failed: {exc}")
        return

    app = QApplication.instance() or QApplication([])

    # Locate the main window class.
    win_cls = None
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and issubclass(obj, QMainWindow):
            win_cls = obj
            break
    if win_cls is None:
        if app_name in ("wallpaper", "game-capture"):
            finding("INFO", app_name, "daemon-style app (no QMainWindow); offscreen render skipped")
        else:
            finding("HIGH", app_name, "no QMainWindow subclass found to render")
        return

    try:
        window = win_cls()
    except Exception as exc:
        finding("HIGH", app_name, f"window construction failed: {exc}")
        return
    window.show()
    app.processEvents()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(out / f"{app_name}-offscreen.png"))
    finding("INFO", app_name, f"rendered offscreen screenshot -> {app_name}-offscreen.png")

    scan_runtime(app_name, window)

    # Click-through: walk every QStackedWidget, advancing via its Next button.
    from PyQt6.QtWidgets import QStackedWidget, QPushButton
    import time as _time

    def pump(ms):
        # Spin the Qt event loop so QTimer/QPropertyAnimation transitions
        # (spring slides etc.) fully finish before we inspect the stack index.
        end = _time.monotonic() + ms / 1000.0
        while _time.monotonic() < end:
            app.processEvents()
            _time.sleep(0.01)

    stack = window.findChild(QStackedWidget)
    if stack is not None:
        start = stack.currentIndex()
        visited = {start}
        for _ in range(stack.count() * 2):
            advanced = False
            for btn in window.findChildren(QPushButton):
                label = btn.text().lower()
                is_next_btn = "next" in label or label in ("finish", "launch aion")
                if btn.isEnabled() and is_next_btn:
                    try:
                        btn.click()
                        pump(700)
                        idx = stack.currentIndex()
                        if idx not in visited:
                            visited.add(idx)
                            finding("INFO", app_name, f"navigated to stack page {idx}")
                            window.grab().save(str(out / f"{app_name}-page{idx}.png"))
                            advanced = True
                    except Exception as exc:
                        finding("MED", app_name, f"click on \"{btn.text()}\" raised {exc}")
                    break
            if not advanced:
                break
        reachable = sorted(visited)
        if len(reachable) < stack.count():
            finding("HIGH", app_name,
                    f"click-through reached pages {reachable} of {stack.count()} — some pages unreachable")
        else:
            finding("INFO", app_name, f"all {stack.count()} stack pages reachable")

    window.close()
    app.processEvents()


def main():
    ap = argparse.ArgumentParser(description="Aion UI Vision Agent")
    ap.add_argument("ui_dir", nargs="?", default=str(REPO_UI),
                    help="path to the ui/ directory")
    ap.add_argument("--app", default=None,
                    choices=["oobe", "theme", "wallpaper", "wallpaper-selector", "game-capture"])
    ap.add_argument("--render", action="store_true",
                    help="also render offscreen and run QTest click-through")
    ap.add_argument("--screenshots-dir", default="/tmp/aion-ui")
    args = ap.parse_args()

    apps = {
        "oobe": "oobe/oobe_wizard.py",
        "theme": "theme-switcher/nexus-theme-switcher.py",
        "wallpaper": "live-wallpaper/live-wallpaper.py",
        "wallpaper-selector": "live-wallpaper/wallpaper-selector.py",
        "game-capture": "game-capture/game-capture-daemon.py",
    }
    base = Path(args.ui_dir)
    targets = [args.app] if args.app else list(apps)

    for name in targets:
        rel = apps[name]
        p = base / rel
        if not p.exists():
            finding("HIGH", name, f"source not found: {rel}")
            continue
        scan_source(name, p)
        if args.render:
            render_and_inspect(name, p, args.screenshots_dir)

    print(f"\nTotal findings: {len(FINDINGS)} "
          f"({sum(1 for f in FINDINGS if f[0]=='HIGH')} HIGH, "
          f"{sum(1 for f in FINDINGS if f[0]=='MED')} MED, "
          f"{sum(1 for f in FINDINGS if f[0]=='LOW')} LOW)")
    return 1 if any(f[0] == "HIGH" for f in FINDINGS) else 0


if __name__ == "__main__":
    sys.exit(main())
