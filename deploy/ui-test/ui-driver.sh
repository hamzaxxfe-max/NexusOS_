#!/usr/bin/env bash
#
# Aion UI Vision-Test Driver (GitHub Actions / headless Linux).
#
# 1. Starts a virtual display (Xvfb).
# 2. Stages the repo's UI files into their deployed paths
#    (/usr/lib/aion/..., /opt/aion/ui/...) so the apps can find configs.
# 3. Runs ui-capture.sh for every interface.
# 4. Scans the captured logs for tracebacks/errors and emits
#    a machine-readable report consumed by the vision agent.
#
set -u

ROOT="${ROOT:-/workspace/repo}"
DISP=":99"
SCREENSIZE="${SCREENSIZE:-1440x900x24}"
CAPTURE="$(cd "$(dirname "$0")" && pwd)/ui-capture.sh"
OUT="${OUT:-/tmp/aion-ui}"

log() { echo "[ui-driver] $*"; }

stage() {
    log "staging UI files into deployed paths"
    # oobe wizard -> /usr/lib/aion/oobe/
    install -D -m 0755 "${ROOT}/ui/oobe/oobe_wizard.py" /usr/lib/aion/oobe/oobe_wizard.py
    # theme switcher -> /usr/lib/aion/theme-switcher/
    install -D -m 0755 "${ROOT}/ui/theme-switcher/nexus-theme-switcher.py" /usr/lib/aion/theme-switcher/nexus-theme-switcher.py
    # live wallpaper -> /opt/aion/ui/live-wallpaper/
    install -D -m 0755 "${ROOT}/ui/live-wallpaper/live-wallpaper.py" /opt/aion/ui/live-wallpaper/live-wallpaper.py
    install -D -m 0755 "${ROOT}/ui/live-wallpaper/wallpaper-selector.py" /opt/aion/ui/live-wallpaper/wallpaper-selector.py
    # game capture -> /opt/aion/ui/game-capture/
    install -D -m 0755 "${ROOT}/ui/game-capture/game-capture-daemon.py" /opt/aion/ui/game-capture/game-capture-daemon.py
    # config dirs the apps expect
    mkdir -p /etc/aion /usr/share/aion /var/log/aion
    install -m 0644 "${ROOT}/config/"*.json /etc/aion/ 2>/dev/null || true
    install -m 0644 "${ROOT}/ui/oobe/../live-wallpaper/live-wallpaper.json" /etc/aion/live-wallpaper.json 2>/dev/null || true
    install -m 0644 "${ROOT}/ui/game-capture/capture-config.json" /etc/aion/capture-config.json 2>/dev/null || true
}

start_xvfb() {
    if ! command -v Xvfb >/dev/null 2>&1; then
        log "FATAL: Xvfb not installed"; exit 1
    fi
    Xvfb "${DISP}" -screen 0 "${SCREENSIZE}" >"${OUT}/xvfb.log" 2>&1 &
    echo $! > "${OUT}/xvfb.pid"
    sleep 2
    export DISPLAY="${DISP}"
}

gen_report() {
    log "generating error report"
    local report="${OUT}/report.md"
    {
        echo "# Aion UI Vision Test Report"
        echo ""
        echo "- Timestamp: $(date -u)"
        echo "- Interface: full headless render (Xvfb ${DISP})"
        echo ""
        echo "## Crash / traceback scan"
        echo ""
        local found=0
        for lg in "${OUT}"/*.log; do
            [[ -f "${lg}" ]] || continue
            if grep -Eq "Traceback|Error|Exception|FATAL" "${lg}"; then
                found=1
                echo "### \`$(basename "${lg}")\`"
                echo '```'
                grep -nE "Traceback|Error|Exception|FATAL|line [0-9]+" "${lg}" | head -20
                echo '```'
            fi
        done
        if [[ "${found}" -eq 0 ]]; then
            echo "No tracebacks detected in captured logs."
        fi
        echo ""
        echo "## Screenshots"
        echo ""
        for png in "${OUT}"/*.png; do
            [[ -f "${png}" ]] || continue
            echo "- ![]($(basename "${png}")) — \`$(basename "${png}")\`"
        done
        echo ""
        echo "## Vision-agent instructions"
        echo ""
        echo "Each screenshot above shows a rendered Aion interface. Inspect each image,"
        echo "drive the UI with xdotool on ${DISP}, and report: layout breakage, truncated"
        echo "text, missing controls, dead buttons, wrong labels, and crash dialogs."
    } > "${report}"
    log "report written: ${report}"
}

main() {
    mkdir -p "${OUT}"
    rm -f "${OUT}"/*.png "${OUT}"/*.log "${OUT}"/*.done 2>/dev/null || true
    stage
    start_xvfb
    export DISPLAY="${DISP}"
    export OUT_DIR="${OUT}"
    # Note: xdotool is used interactively by the vision agent after this point.
    chmod +x "${CAPTURE}"
    bash "${CAPTURE}" --display "${DISP}" --out "${OUT}" --app "${APP:-all}"
    gen_report
    if command -v xdpyinfo >/dev/null 2>&1; then xdpyinfo >/dev/null 2>&1; fi
}

main
