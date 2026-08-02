#!/bin/bash
set -euo pipefail
# Aion Per-App Audio Routing — PipeWire virtual sinks and WirePlumber auto-route

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG_FILE="/var/log/aion/audio-routing.log"
WP_SCRIPT_DIR="/usr/share/wireplumber/scripts"
WP_LUA_DIR="/etc/wireplumber/lua"

GAMING_SINK="aion-gaming"
CHAT_SINK="aion-chat"
MEDIA_SINK="aion-media"

log_info()    { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")"

get_default_sink() {
    local default_sink
    default_sink=$(pactl get-default-sink 2>/dev/null || echo "")
    if [ -z "$default_sink" ]; then
        log_warn "No default PulseAudio/PipeWire sink found"
        default_sink="auto_null"
    fi
    echo "$default_sink"
}

sinks_exist() {
    local count=0
    if command -v pactl &>/dev/null; then
        count=$(pactl list short sinks 2>/dev/null | grep -c "aion-" || echo "0")
    fi
    [ "$count" -ge 3 ]
}

create_virtual_sinks() {
    log_info "Creating virtual audio sinks"
    local default_sink
    default_sink=$(get_default_sink)

    for sink_name in "$GAMING_SINK" "$CHAT_SINK" "$MEDIA_SINK"; do
        if pactl list short sinks 2>/dev/null | grep -q "$sink_name"; then
            log_info "Sink $sink_name already exists — skipping"
            continue
        fi
        pactl load-module module-null-sink \
            sink_name="$sink_name" \
            sink_properties="node.description=Aion-${sink_name#aion-}" \
            2>/dev/null && log_success "Created sink: $sink_name" \
            || log_error "Failed to create sink: $sink_name"
    done
}

create_loopbacks() {
    log_info "Creating loopback modules (virtual sinks -> physical output)"
    local default_sink
    default_sink=$(get_default_sink)

    for sink_name in "$GAMING_SINK" "$CHAT_SINK" "$MEDIA_SINK"; do
        local loopback_name="${sink_name}-loopback"
        if pactl list short modules 2>/dev/null | grep -q "$loopback_name"; then
            log_info "Loopback $loopback_name already exists — skipping"
            continue
        fi
        pactl load-module module-loopback \
            source="${sink_name}.monitor" \
            sink="$default_sink" \
            2>/dev/null && log_success "Created loopback: $sink_name -> $default_sink" \
            || log_error "Failed to create loopback: $sink_name"
    done
}

install_wireplumber_rules() {
    log_info "Installing WirePlumber auto-routing rules"

    mkdir -p "$WP_LUA_DIR"
    cat > "${WP_LUA_DIR}/99-aion-routing.lua" <<'LUAEOF'
-- Aion WirePlumber auto-routing rules
-- Routes gaming apps to aion-gaming sink, etc.

rule {
    matches = {
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*steam*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*wine*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*proton*" },
        },
    },
    apply_properties = {
        ["target.node"] = "aion-gaming",
    },
}

rule {
    matches = {
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*discord*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*teamspeak*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*mumble*" },
        },
    },
    apply_properties = {
        ["target.node"] = "aion-chat",
    },
}

rule {
    matches = {
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*firefox*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*spotify*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*chromium*" },
        },
        {
            { "media.class", "equals", "Stream/Output/Audio" },
            { "node.name",   "matches", "*mpv*" },
        },
    },
    apply_properties = {
        ["target.node"] = "aion-media",
    },
}
LUAEOF
    log_success "WirePlumber routing rules installed at ${WP_LUA_DIR}/99-aion-routing.lua"
}

teardown() {
    log_info "Tearing down audio routing"

    for sink_name in "$GAMING_SINK" "$CHAT_SINK" "$MEDIA_SINK"; do
        local loopback_name="${sink_name}-loopback"
        local module_ids
        module_ids=$(pactl list short modules 2>/dev/null | grep "$loopback_name" | awk '{print $1}' || echo "")
        for mid in $module_ids; do
            pactl unload-module "$mid" 2>/dev/null && log_info "Unloaded loopback module $mid" || true
        done
    done

    for sink_name in "$GAMING_SINK" "$CHAT_SINK" "$MEDIA_SINK"; do
        local module_ids
        module_ids=$(pactl list short sinks 2>/dev/null | grep "$sink_name" | awk '{print $1}' || echo "")
        for mid in $module_ids; do
            pactl unload-module "$mid" 2>/dev/null && log_info "Unloaded sink module for $sink_name" || true
        done
    done

    local wp_rule="${WP_LUA_DIR}/99-aion-routing.lua"
    if [ -f "$wp_rule" ]; then
        rm -f "$wp_rule"
        log_info "Removed WirePlumber routing rules"
    fi

    log_success "Audio routing torn down"
}

route_app() {
    local app_pattern="$1"
    local target_sink="$2"

    if [ -z "$app_pattern" ] || [ -z "$target_sink" ]; then
        log_error "Usage: aion-audio-route.sh route <app-pattern> <sink-name>"
        return 1
    fi

    case "$target_sink" in
        gaming) target_sink="$GAMING_SINK" ;;
        chat)   target_sink="$CHAT_SINK" ;;
        media)  target_sink="$MEDIA_SINK" ;;
    esac

    log_info "Routing streams matching '$app_pattern' to $target_sink"
    local stream_ids
    stream_ids=$(pactl list short sink-inputs 2>/dev/null | grep -i "$app_pattern" | awk '{print $1}' || echo "")

    if [ -z "$stream_ids" ]; then
        log_warn "No active streams found matching '$app_pattern'"
        return 0
    fi

    for sid in $stream_ids; do
        pactl move-sink-input "$sid" "$target_sink" 2>/dev/null \
            && log_success "Moved stream $sid to $target_sink" \
            || log_warn "Failed to move stream $sid"
    done
}

show_status() {
    log_info "=== Aion Audio Routing Status ==="

    echo -e "\n${CYAN}Virtual Sinks:${NC}"
    if command -v pactl &>/dev/null; then
        pactl list short sinks 2>/dev/null | grep "aion-" || echo "  (none)"
    else
        echo "  pactl not available"
    fi

    echo -e "\n${CYAN}Loopback Modules:${NC}"
    if command -v pactl &>/dev/null; then
        pactl list short modules 2>/dev/null | grep "loopback" || echo "  (none)"
    fi

    echo -e "\n${CYAN}Active Sink Inputs:${NC}"
    if command -v pactl &>/dev/null; then
        pactl list short sink-inputs 2>/dev/null || echo "  (none)"
    fi

    echo -e "\n${CYAN}WirePlumber Rules:${NC}"
    if [ -f "${WP_LUA_DIR}/99-aion-routing.lua" ]; then
        echo "  Installed: ${WP_LUA_DIR}/99-aion-routing.lua"
    else
        echo "  Not installed"
    fi

    echo -e "\n${CYAN}Default Sink:${NC}"
    echo "  $(get_default_sink)"
}

show_usage() {
    cat <<EOF
Aion Audio Routing — PipeWire per-app audio management

Usage: $(basename "$0") <command>

Commands:
    --setup       Create virtual sinks, loopbacks, and WirePlumber rules
    --teardown    Remove all virtual sinks and routing rules
    --route       Route a specific app: $(basename "$0") --route <app> <sink>
    --status      Show current audio routing state
    --help        Show this help

Sinks:
    gaming    aion-gaming  — Steam, Wine, Proton, game audio
    chat      aion-chat    — Discord, TeamSpeak, Mumble
    media     aion-media   — Firefox, Spotify, Chromium, mpv
EOF
}

main() {
    local cmd="${1:---help}"

    case "$cmd" in
        --setup)
            log_info "=== Audio Routing Setup ==="
            create_virtual_sinks
            create_loopbacks
            install_wireplumber_rules
            log_success "=== Audio Routing Setup Complete ==="
            ;;
        --teardown)
            teardown
            ;;
        --route)
            local app="${2:-}"
            local sink="${3:-}"
            route_app "$app" "$sink"
            ;;
        --status)
            show_status
            ;;
        --help|-h|*)
            show_usage
            ;;
    esac
}

main "$@"
