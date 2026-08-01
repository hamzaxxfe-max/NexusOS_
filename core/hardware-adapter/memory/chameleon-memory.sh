#!/bin/bash
set -euo pipefail
# Aion Chameleon Memory Factory — Adaptive ZRAM and memory management

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

CONFIG_DIR="/etc/aion"
OUTPUT_FILE="${CONFIG_DIR}/memory-config.json"
LOG_FILE="/var/log/aion/chameleon-memory.log"

log_info()  { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")" "$CONFIG_DIR"

detect_ram_kb() {
    local ram_kb
    ram_kb=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo 2>/dev/null || echo "0")
    echo "${ram_kb:-0}"
}

detect_ram_gb() {
    local ram_kb
    ram_kb=$(detect_ram_kb)
    local ram_gb=$((ram_kb / 1024 / 1024))
    if [ "$ram_gb" -lt 1 ]; then
        ram_gb=1
    fi
    echo "$ram_gb"
}

calculate_tier() {
    local ram_gb="$1"
    local tier zram_ratio swappiness dirty_ratio dirty_bg_ratio compact_memory
    if [ "$ram_gb" -le 4 ]; then
        tier=1
        zram_ratio=4.0
        swappiness=200
        dirty_ratio=20
        dirty_bg_ratio=5
        compact_memory=1
    elif [ "$ram_gb" -le 8 ]; then
        tier=2
        zram_ratio=2.0
        swappiness=160
        dirty_ratio=15
        dirty_bg_ratio=3
        compact_memory=1
    elif [ "$ram_gb" -le 16 ]; then
        tier=3
        zram_ratio=1.5
        swappiness=120
        dirty_ratio=10
        dirty_bg_ratio=2
        compact_memory=0
    elif [ "$ram_gb" -le 32 ]; then
        tier=4
        zram_ratio=1.0
        swappiness=80
        dirty_ratio=8
        dirty_bg_ratio=1
        compact_memory=0
    else
        tier=5
        zram_ratio=1.0
        swappiness=40
        dirty_ratio=5
        dirty_bg_ratio=1
        compact_memory=0
    fi
    echo "$tier $zram_ratio $swappiness $dirty_ratio $dirty_bg_ratio $compact_memory"
}

setup_zram() {
    local zram_ratio="$1"
    local ram_kb
    ram_kb=$(detect_ram_kb)
    local zram_size_kb=$(( ram_kb * ${zram_ratio%.*} ))
    local zram_size_mb=$(( zram_size_kb / 1024 ))
    log_info "Setting up ZRAM: ${zram_size_mb} MiB (ratio ${zram_ratio}:1)"
    modprobe zram 2>/dev/null || true
    if [ ! -d "/sys/block/zram0" ]; then
        log_warn "zram0 block device not found — trying to create"
        echo "zstd" > /sys/block/zram0/comp_algorithm 2>/dev/null || true
    fi
    if [ -f "/sys/block/zram0/disksize" ]; then
        local current_size
        current_size=$(cat /sys/block/zram0/disksize 2>/dev/null || echo "0")
        local target_bytes=$(( zram_size_kb * 1024 ))
        if [ "$current_size" != "$target_bytes" ]; then
            echo 1 > /sys/block/zram0/reset 2>/dev/null || true
            echo "$target_bytes" > /sys/block/zram0/disksize 2>/dev/null || {
                log_error "Failed to set ZRAM disksize"
                return 1
            }
            log_success "ZRAM disksize set to $target_bytes bytes"
        else
            log_info "ZRAM disksize already at target"
        fi
    fi
    echo "zstd" > /sys/block/zram0/comp_algorithm 2>/dev/null || true
    log_success "ZRAM compression: zstd"
    local mem_free
    mem_free=$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo 2>/dev/null || echo "0")
    if [ "$mem_free" -gt 0 ]; then
        local max_used=$(( mem_free * 80 / 100 ))
        echo "$max_used" > /sys/block/zram0/mm_stat 2>/dev/null || true
    fi
    if [ -b "/dev/zram0" ]; then
        mkswap /dev/zram0 2>/dev/null && {
            swapon -p 100 /dev/zram0 2>/dev/null || true
            log_success "ZRAM swap activated"
        }
    fi
    echo "$zram_size_mb"
}

configure_swappiness() {
    local swappiness="$1"
    local current
    current=$(cat /proc/sys/vm/swappiness 2>/dev/null || echo "0")
    if [ "$current" != "$swappiness" ]; then
        sysctl -w vm.swappiness="$swappiness" >/dev/null 2>&1 || true
        log_success "swappiness: $current -> $swappiness"
    else
        log_info "swappiness already at $swappiness"
    fi
}

configure_dirty_ratio() {
    local dirty_ratio="$1"
    local dirty_bg_ratio="$2"
    local current_ratio current_bg
    current_ratio=$(cat /proc/sys/vm/dirty_ratio 2>/dev/null || echo "0")
    current_bg=$(cat /proc/sys/vm/dirty_background_ratio 2>/dev/null || echo "0")
    if [ "$current_ratio" != "$dirty_ratio" ]; then
        sysctl -w vm.dirty_ratio="$dirty_ratio" >/dev/null 2>&1 || true
        log_success "dirty_ratio: $current_ratio -> $dirty_ratio"
    fi
    if [ "$current_bg" != "$dirty_bg_ratio" ]; then
        sysctl -w vm.dirty_background_ratio="$dirty_bg_ratio" >/dev/null 2>&1 || true
        log_success "dirty_background_ratio: $current_bg -> $dirty_bg_ratio"
    fi
}

configure_compaction() {
    local compact="$1"
    if [ "$compact" = "1" ]; then
        sysctl -w vm.compact_unevictable_allowed=1 >/dev/null 2>&1 || true
        log_info "Memory compaction enabled (Tier 1-2)"
    else
        log_info "Memory compaction at default (Tier 3+)"
    fi
}

write_config() {
    local tier="$1" zram_ratio="$2" swappiness="$3" dirty_ratio="$4" dirty_bg_ratio="$5" compact="$6"
    local ram_gb ram_kb zram_size_mb
    ram_gb=$(detect_ram_gb)
    ram_kb=$(detect_ram_kb)
    zram_size_mb=$(( ram_kb * ${zram_ratio%.*} / 1024 ))
    cat > "$OUTPUT_FILE" <<EOF
{
  "tier": $tier,
  "total_ram_gb": $ram_gb,
  "total_ram_kb": $ram_kb,
  "zram": {
    "enabled": true,
    "size_mb": $zram_size_mb,
    "ratio": $zram_ratio,
    "algorithm": "zstd",
    "compression_level": 3
  },
  "swappiness": $swappiness,
  "dirty_ratio": $dirty_ratio,
  "dirty_background_ratio": $dirty_bg_ratio,
  "compact_unevictable": $([ "$compact" = "1" ] && echo "true" || echo "false"),
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    log_success "Memory config written to $OUTPUT_FILE"
}

report_status() {
    log_info "--- Memory Status ---"
    if command -v free &>/dev/null; then
        free -h 2>/dev/null | while IFS= read -r line; do
            log_info "  $line"
        done
    fi
    if [ -f "/sys/block/zram0/disksize" ]; then
        local zram_size
        zram_size=$(cat /sys/block/zram0/disksize 2>/dev/null || echo "0")
        local zram_mb=$(( zram_size / 1024 / 1024 ))
        log_info "  ZRAM: ${zram_mb} MiB"
    fi
    local swappiness
    swappiness=$(cat /proc/sys/vm/swappiness 2>/dev/null || echo "N/A")
    log_info "  Swappiness: $swappiness"
    log_info "--- End Status ---"
}

main() {
    log_info "=== Aion Chameleon Memory Factory starting ==="
    local ram_gb
    ram_gb=$(detect_ram_gb)
    log_info "Detected ${ram_gb} GiB RAM"
    read -r tier zram_ratio swappiness dirty_ratio dirty_bg_ratio compact <<< "$(calculate_tier "$ram_gb")"
    log_info "Selected Tier $tier: zram_ratio=$zram_ratio swappiness=$swappiness"
    setup_zram "$zram_ratio"
    configure_swappiness "$swappiness"
    configure_dirty_ratio "$dirty_ratio" "$dirty_bg_ratio"
    configure_compaction "$compact"
    write_config "$tier" "$zram_ratio" "$swappiness" "$dirty_ratio" "$dirty_bg_ratio" "$compact"
    report_status
    log_info "=== Chameleon Memory Factory complete ==="
}

main "$@"
