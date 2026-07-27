#!/bin/bash
set -euo pipefail
# NexusOS Storage Optimizer — Detect storage, set schedulers, optimize Btrfs

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

CONFIG_DIR="/etc/nexusos"
OUTPUT_FILE="${CONFIG_DIR}/storage-profile.json"
LOG_FILE="/var/log/nexusos/storage-optimizer.log"

log_info()  { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")" "$CONFIG_DIR"

detect_device_type() {
    local dev="$1"
    local base_dev
    base_dev=$(basename "$dev")
    local rotational="0"
    local rot_file="/sys/block/${base_dev}/queue/rotational"
    if [ -f "$rot_file" ]; then
        rotational=$(cat "$rot_file" 2>/dev/null || echo "0")
    fi
    if [ "$rotational" = "0" ]; then
        if [ -d "/sys/block/${base_dev}/nvme" ] || [[ "$base_dev" == nvme* ]]; then
            echo "nvme"
        else
            echo "ssd"
        fi
    else
        echo "hdd"
    fi
}

detect_devices() {
    local devices=()
    for dev_path in /sys/block/*/; do
        local dev_name
        dev_name=$(basename "$dev_path")
        case "$dev_name" in
            loop*|ram*|zram*|dm-*) continue ;;
        esac
        if [ -d "/sys/block/$dev_name/device" ]; then
            devices+=("$dev_name")
        fi
    done
    echo "${devices[@]}"
}

get_device_size_gb() {
    local dev="$1"
    local size_bytes=0
    local size_file="/sys/block/${dev}/size"
    if [ -f "$size_file" ]; then
        local sectors
        sectors=$(cat "$size_file" 2>/dev/null || echo "0")
        size_bytes=$(( sectors * 512 ))
    fi
    echo $(( size_bytes / 1024 / 1024 / 1024 ))
}

get_queue_depth() {
    local dev="$1"
    local depth_file="/sys/block/${dev}/queue/nr_requests"
    if [ -f "$depth_file" ]; then
        cat "$depth_file" 2>/dev/null || echo "32"
    else
        echo "32"
    fi
}

set_scheduler() {
    local dev="$1"
    local dev_type="$2"
    local scheduler_file="/sys/block/${dev}/queue/scheduler"
    if [ ! -f "$scheduler_file" ]; then
        return
    fi
    local available
    available=$(cat "$scheduler_file" 2>/dev/null || echo "")
    local target=""
    case "$dev_type" in
        nvme)
            for s in mq-deadline none kyber bfq; do
                if echo "$available" | grep -q "\[$s\]"; then
                    target="$s"
                    break
                fi
            done
            ;;
        ssd)
            for s in mq-deadline bfq none kyber; do
                if echo "$available" | grep -q "\[$s\]"; then
                    target="$s"
                    break
                fi
            done
            ;;
        hdd)
            for s in bfq mq-deadline; do
                if echo "$available" | grep -q "\[$s\]"; then
                    target="$s"
                    break
                fi
            done
            ;;
    esac
    if [ -n "$target" ]; then
        echo "[$target]" > "$scheduler_file" 2>/dev/null || true
        log_success "$dev: scheduler -> $target ($dev_type)"
    fi
}

set_readahead() {
    local dev="$1"
    local dev_type="$2"
    local readahead_kb=""
    case "$dev_type" in
        nvme) readahead_kb="256" ;;
        ssd)  readahead_kb="512" ;;
        hdd)  readahead_kb="2048" ;;
    esac
    if [ -n "$readahead_kb" ]; then
        blockdev --setra "$readahead_kb" "/dev/$dev" 2>/dev/null || true
        log_success "$dev: readahead -> ${readahead_kb}KB"
    fi
}

optimize_btrfs_mount() {
    local dev="$1"
    local dev_type="$2"
    local btrfs_opts="compress-force=zstd:3"
    case "$dev_type" in
        nvme|ssd) btrfs_opts+=",ssd" ;;
        hdd)      btrfs_opts+=",nodiscard" ;;
    esac
    btrfs_opts+=",space_cache=v2"
    log_info "$dev: recommended Btrfs mount options: $btrfs_opts"
    echo "$btrfs_opts"
}

check_health() {
    local dev="$1"
    if ! command -v smartctl &>/dev/null; then
        echo "smartctl not available"
        return
    fi
    local smart_out
    smart_out=$(smartctl -H "/dev/$dev" 2>&1 || true)
    if echo "$smart_out" | grep -q "PASSED\|OK\|Healthy"; then
        echo "healthy"
    elif echo "$smart_out" | grep -q "FAILED\|CRITICAL\|FAILING"; then
        echo "failing"
    else
        echo "unknown"
    fi
}

output_json() {
    local devices_str="$1"
    local dev_types_str="$2"
    local dev_sizes_str="$3"
    local dev_health_str="$4"
    local dev_schedulers_str="$5"
    local dev_readaheads_str="$6"
    local dev_btrfs_opts_str="$7"
    local dev_count
    dev_count=$(echo "$devices_str" | wc -w)
    dev_count=${dev_count:-0}
    local devices_json="["
    local first=true
    for i in $(seq 0 $((dev_count - 1))); do
        local dev dev_type size_gb health scheduler readahead btrfs_opts
        dev=$(echo "$devices_str" | cut -d' ' -f$((i + 1)))
        dev_type=$(echo "$dev_types_str" | cut -d' ' -f$((i + 1)))
        size_gb=$(echo "$dev_sizes_str" | cut -d' ' -f$((i + 1)))
        health=$(echo "$dev_health_str" | cut -d' ' -f$((i + 1)))
        scheduler=$(echo "$dev_schedulers_str" | cut -d' ' -f$((i + 1)))
        readahead=$(echo "$dev_readaheads_str" | cut -d' ' -f$((i + 1)))
        btrfs_opts=$(echo "$dev_btrfs_opts_str" | cut -d' ' -f$((i + 1)))
        if [ "$first" = true ]; then
            first=false
        else
            devices_json+=","
        fi
        devices_json+="{\"name\":\"$dev\",\"type\":\"$dev_type\",\"size_gb\":$size_gb,\"health\":\"$health\",\"scheduler\":\"$scheduler\",\"readahead_kb\":$readahead,\"btrfs_options\":\"$btrfs_opts\"}"
    done
    devices_json+="]"
    cat > "$OUTPUT_FILE" <<EOF
{
  "device_count": $dev_count,
  "devices": $devices_json,
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    log_success "Storage profile written to $OUTPUT_FILE"
}

main() {
    log_info "=== NexusOS Storage Optimizer starting ==="
    local devices_str
    devices_str=$(detect_devices)
    if [ -z "$devices_str" ]; then
        log_warn "No block devices detected"
        return
    fi
    local dev_types="" dev_sizes="" dev_health="" dev_schedulers="" dev_readaheads="" dev_btrfs_opts=""
    for dev in $devices_str; do
        log_info "Processing /dev/$dev"
        local dev_type size_gb health scheduler readahead_kb btrfs_opts
        dev_type=$(detect_device_type "$dev")
        size_gb=$(get_device_size_gb "$dev")
        set_scheduler "$dev" "$dev_type"
        set_readahead "$dev" "$dev_type"
        btrfs_opts=$(optimize_btrfs_mount "$dev" "$dev_type")
        health=$(check_health "$dev")
        scheduler=$(cat "/sys/block/${dev}/queue/scheduler" 2>/dev/null | grep -o '\[.*\]' | tr -d '[]' || echo "none")
        readahead_kb=$(blockdev --getra "/dev/$dev" 2>/dev/null || echo "0")
        dev_types+=" $dev_type"
        dev_sizes+=" $size_gb"
        dev_health+=" $health"
        dev_schedulers+=" $scheduler"
        dev_readaheads+=" $readahead_kb"
        dev_btrfs_opts+=" $btrfs_opts"
        log_success "$dev: type=$dev_type size=${size_gb}GB health=$health scheduler=$scheduler"
    done
    output_json "$devices_str" "$dev_types" "$dev_sizes" "$dev_health" "$dev_schedulers" "$dev_readaheads" "$dev_btrfs_opts"
    log_info "=== Storage Optimizer complete ==="
}

main "$@"
