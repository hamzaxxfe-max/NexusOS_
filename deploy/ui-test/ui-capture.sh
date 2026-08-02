#!/usr/bin/env bash
#
# Aion UI Visual Capture — headless screenshot harness.
#
# Runs each Aion PyQt6 interface under a virtual X display (Xvfb) and
# captures a PNG of every state. This is the "eyes" of the vision agent:
# the screenshots are handed to a vision-capable model which drives the UI
# via xdotool and reports user-facing defects.
#
# Usage:  ui-capture.sh  [--display :99] [--out DIR] [--app APP]
#
#   --app oobe|theme|wallpaper|wallpaper-selector|game-capture
#
set -u

DISPLAY_NUM=":99"
OUT_DIR="${OUT_DIR:-/tmp/aion-ui}"
APP="${APP:-all}"
WIZARD="${WIZARD:-/usr/lib/aion/oobe/oobe_wizard.py}"
THEME_SWITCHER="${THEME_SWITCHER:-/usr/lib/aion/theme-switcher/nexus-theme-switcher.py}"
LIVE_WALLPAPER="${LIVE_WALLPAPER:-/opt/aion/ui/live-wallpaper/live-wallpaper.py}"
WALLPAPER_SELECTOR="${WALLPAPER_SELECTOR:-/opt/aion/ui/live-wallpaper/wallpaper-selector.py}"
GAME_CAPTURE="${GAME_CAPTURE:-/opt/aion/ui/game-capture/game-capture-daemon.py}"

mkdir -p "${OUT_DIR}"
export DISPLAY="${DISPLAY_NUM}"

log() { echo "[ui-capture] $*"; }

shoot() { # shoot <name>
    # Give Qt a moment to paint the current frame.
    sleep 1.5
    timeout 15 import -window root "${OUT_DIR}/${1}.png" 2>/dev/null \
        && log "captured ${1}.png" \
        || log "WARN: failed to capture ${1}.png"
}

run_app() { # run_app <label> <python-file>
    local label="$1" file="$2"
    if [[ ! -f "${file}" ]]; then
        log "SKIP ${label}: ${file} not present"
        return 0
    fi
    log "launching ${label} (${file})"
    python3 "${file}" >"${OUT_DIR}/${label}.log" 2>&1 &
    local pid=$!
    sleep 4
    if ! kill -0 "${pid}" 2>/dev/null; then
        log "ERROR: ${label} exited immediately — see ${label}.log"
        return 0
    fi
    shoot "${label}-initial"
    log "  ${label} alive (pid ${pid}), waiting for interaction..."
    # If no interactive vision agent is present (AUTO_DONE=1), create the
    # .done marker ourselves after a short settle so captures don't block CI.
    if [[ "${AUTO_DONE:-0}" == "1" ]]; then
        sleep "${AUTO_DONE_SECS:-8}"
        touch "${OUT_DIR}/${label}.done"
    fi
    # Let the vision agent drive it; wait for the .done marker file.
    local waited=0
    while [[ ! -f "${OUT_DIR}/${label}.done" && "${waited}" -lt 60 ]]; do
        sleep 2
        waited=$((waited + 2))
    done
    shoot "${label}-final"
    kill "${pid}" 2>/dev/null || true
    # Graceful shutdown with a hard ceiling — an app that ignores SIGTERM
    # must not wedge the capture driver (CI timeout).
    local grace=0
    while [[ "${grace}" -lt 10 ]] && kill -0 "${pid}" 2>/dev/null; do
        sleep 1
        grace=$((grace + 1))
    done
    if kill -0 "${pid}" 2>/dev/null; then
        log "  ${label} ignored SIGTERM — sending SIGKILL"
        kill -9 "${pid}" 2>/dev/null || true
    fi
    wait "${pid}" 2>/dev/null || true
    rm -f "${OUT_DIR}/${label}.done"
    log "finished ${label}"
}

main() {
    log "starting on ${DISPLAY_NUM}, output ${OUT_DIR}"
    case "${APP}" in
        oobe)              run_app "oobe"               "${WIZARD}" ;;
        theme)             run_app "theme-switcher"     "${THEME_SWITCHER}" ;;
        wallpaper)         run_app "live-wallpaper"     "${LIVE_WALLPAPER}" ;;
        wallpaper-selector)run_app "wallpaper-selector" "${WALLPAPER_SELECTOR}" ;;
        game-capture)      run_app "game-capture"       "${GAME_CAPTURE}" ;;
        all)
            run_app "oobe"               "${WIZARD}"
            run_app "theme-switcher"     "${THEME_SWITCHER}"
            run_app "live-wallpaper"     "${LIVE_WALLPAPER}"
            run_app "wallpaper-selector" "${WALLPAPER_SELECTOR}"
            run_app "game-capture"       "${GAME_CAPTURE}"
            ;;
        *) log "unknown app: ${APP}"; exit 2 ;;
    esac
    log "captures written to ${OUT_DIR}"
}

main
