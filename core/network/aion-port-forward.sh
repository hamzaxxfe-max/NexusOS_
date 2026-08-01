#!/bin/bash
set -euo pipefail
# Aion Gaming Port Forwarding — UPnP-based router port management

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG_FILE="/var/log/aion/port-forward.log"
STATE_FILE="/run/aion-port-forward-state"

log_info()    { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$STATE_FILE")"

# Gaming port definitions: protocol|external_port|internal_port|description
PORT_RULES=(
    "UDP|27015-27031|27015-27031|Steam"
    "TCP|27036|27036|Steam-RemotePlay"
    "UDP|3478|3478|STUN-NAT"
    "TCP|3478|3478|STUN-NAT"
    "UDP|3479|3479|STUN-NAT-Alt"
    "TCP|3479|3479|STUN-NAT-Alt"
    "TCP|25565|25565|Minecraft-Server"
    "UDP|25565|25565|Minecraft-Server"
    "UDP|19132|19132|Minecraft-Bedrock"
    "TCP|19132|19132|Minecraft-Bedrock"
    "UDP|3074|3074|Call-of-Duty"
    "TCP|3074|3074|Call-of-Duty"
    "UDP|3075|3075|Destiny-2"
    "TCP|3075|3075|Destiny-2"
    "UDP|27017|27017|Steam-Matchmaking"
    "TCP|27017|27017|Steam-Matchmaking"
    "UDP|30120|30120|GTA-Online"
    "TCP|30120|30120|GTA-Online"
    "UDP|1024-1035|1024-1035|Overwatch"
    "TCP|1024-1035|1024-1035|Overwatch"
    "UDP|6112-6119|6112-6119|Battle.net"
    "TCP|6112-6119|6112-6119|Battle.net"
    "UDP|3724|3724|World-of-Warcraft"
    "TCP|3724|3724|World-of-Warcraft"
    "UDP|8085-8087|8085-8087|StarCraft"
    "TCP|1119|1119|Blizzard-Auth"
    "UDP|53|53|DNS-Alt"
    "TCP|3216|3216|FarmSim"
    "UDP|4380|4380|Steam-P2P"
    "UDP|27000-27030|27000-27030|Steam-Query"
    "TCP|27016|27016|Steam-RCON"
)

get_local_ip() {
    local ip
    ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || echo "")
    if [ -z "$ip" ]; then
        ip=$(ip -4 addr show scope global 2>/dev/null | grep -oP 'inet \K[0-9.]+' | head -1 || echo "")
    fi
    if [ -z "$ip" ]; then
        log_error "Could not determine local IP address"
        return 1
    fi
    echo "$ip"
}

check_upnpc() {
    if ! command -v upnpc &>/dev/null; then
        log_error "upnpc not found — install miniupnpc package"
        return 1
    fi

    local router_ip
    router_ip=$(upnpc -l 2>/dev/null | head -1 | grep -oP 'to \K[0-9.]+' || echo "")
    if [ -z "$router_ip" ]; then
        log_error "Could not discover UPnP router"
        return 1
    fi
    log_info "UPnP router discovered at $router_ip"
}

forward_port() {
    local proto="$1"
    local ext_port="$2"
    local int_port="$3"
    local desc="$4"
    local local_ip="$5"

    if upnpc -a "$local_ip" "$int_port" "$ext_port" "$proto" -d "$desc" &>/dev/null; then
        log_success "Forwarded $proto $ext_port -> $local_ip:$int_port ($desc)"
        return 0
    else
        log_warn "Failed to forward $proto $ext_port ($desc)"
        return 1
    fi
}

remove_port() {
    local proto="$1"
    local ext_port="$2"

    if upnpc -d "$ext_port" "$proto" &>/dev/null; then
        log_info "Removed forward: $proto $ext_port"
    fi
}

start_forwarding() {
    log_info "=== Starting gaming port forwarding ==="

    check_upnpc || return 1

    local local_ip
    local_ip=$(get_local_ip) || return 1
    log_info "Local IP: $local_ip"

    local forwarded=0
    local failed=0

    for rule in "${PORT_RULES[@]}"; do
        IFS='|' read -r proto ports _int_ports desc <<< "$rule"

        if [[ "$ports" == *-* ]]; then
            local start_port end_port
            start_port=$(echo "$ports" | cut -d- -f1)
            end_port=$(echo "$ports" | cut -d- -f2)
            local int_start int_end
            int_start=$(echo "$_int_ports" | cut -d- -f1)
            int_end=$(echo "$_int_ports" | cut -d- -f2)

            local p=$start_port
            local ip=$int_start
            while [ "$p" -le "$end_port" ]; do
                if forward_port "$proto" "$p" "$ip" "$desc" "$local_ip"; then
                    forwarded=$((forwarded + 1))
                else
                    failed=$((failed + 1))
                fi
                p=$((p + 1))
                ip=$((ip + 1))
            done
        else
            if forward_port "$proto" "$ports" "$_int_ports" "$desc" "$local_ip"; then
                forwarded=$((forwarded + 1))
            else
                failed=$((failed + 1))
            fi
        fi
    done

    cat > "$STATE_FILE" <<EOF
{"status":"active","local_ip":"$local_ip","forwarded":$forwarded,"failed":$failed,"timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

    log_success "Port forwarding complete: $forwarded forwarded, $failed failed"
}

stop_forwarding() {
    log_info "=== Removing gaming port forwards ==="

    check_upnpc || return 1

    local removed=0

    for rule in "${PORT_RULES[@]}"; do
        IFS='|' read -r proto ports _int_ports _desc <<< "$rule"

        if [[ "$ports" == *-* ]]; then
            local start_port end_port
            start_port=$(echo "$ports" | cut -d- -f1)
            end_port=$(echo "$ports" | cut -d- -f2)
            local p=$start_port
            while [ "$p" -le "$end_port" ]; do
                remove_port "$proto" "$p"
                removed=$((removed + 1))
                p=$((p + 1))
            done
        else
            remove_port "$proto" "$ports"
            removed=$((removed + 1))
        fi
    done

    cat > "$STATE_FILE" <<EOF
{"status":"inactive","removed":$removed,"timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

    log_success "Removed $removed port forwards"
}

list_forwarding() {
    log_info "=== Current UPnP port forwards ==="
    if command -v upnpc &>/dev/null; then
        upnpc -l 2>/dev/null
    else
        log_error "upnpc not available"
    fi

    echo ""
    echo -e "${CYAN}Aion gaming rules:${NC}"
    for rule in "${PORT_RULES[@]}"; do
        IFS='|' read -r proto ports int_ports desc <<< "$rule"
        printf "  %-8s %-12s -> %-12s %s\n" "$proto" "$ports" "$int_ports" "$desc"
    done
}

show_usage() {
    cat <<EOF
Aion Gaming Port Forwarding — UPnP router management

Usage: $(basename "$0") <command>

Commands:
    start     Forward all gaming ports via UPnP
    stop      Remove all gaming port forwards
    list      Show current forwards and configured rules
    refresh   Stop then start (refresh all forwards)
    help      Show this help

Common gaming ports forwarded:
    Steam: 27015-27031, 27036, 27017, 4380
    Minecraft: 25565, 19132
    CoD/Battlefield: 3074
    Battle.net: 6112-6119
    GTA Online: 30120
    WoW: 3724
EOF
}

main() {
    local cmd="${1:-help}"

    case "$cmd" in
        start)
            start_forwarding
            ;;
        stop)
            stop_forwarding
            ;;
        list)
            list_forwarding
            ;;
        refresh)
            stop_forwarding
            sleep 1
            start_forwarding
            ;;
        help|-h|--help)
            show_usage
            ;;
        *)
            log_error "Unknown command: $cmd"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
