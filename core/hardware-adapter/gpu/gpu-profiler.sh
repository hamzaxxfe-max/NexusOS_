#!/bin/bash
set -euo pipefail
# NexusOS GPU Profiler — Detect and profile all GPU hardware

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

CONFIG_DIR="/etc/nexusos"
OUTPUT_FILE="${CONFIG_DIR}/gpu-profile.json"
LOG_FILE="/var/log/nexusos/gpu-profiler.log"

log_info()  { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")" "$CONFIG_DIR"

detect_nvidia() {
    local gpus=()
    if ! command -v nvidia-smi &>/dev/null; then
        return
    fi
    local gpu_count
    gpu_count=$(nvidia-smi --query-gpu=count --format=csv,noheader,nounits 2>/dev/null | head -1 || echo "0")
    gpu_count=${gpu_count:-0}
    local i=0
    while [ "$i" -lt "$gpu_count" ]; do
        local model driver vram cuda_cores compute_cap bus_id
        model=$(nvidia-smi --query-gpu=name --format=csv,noheader -i "$i" 2>/dev/null || echo "Unknown")
        driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader -i "$i" 2>/dev/null || echo "unknown")
        vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i "$i" 2>/dev/null || echo "0")
        compute_cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader -i "$i" 2>/dev/null || echo "0.0")
        bus_id=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader -i "$i" 2>/dev/null || echo "")
        cuda_cores="0"
        if [ -n "$compute_cap" ] && [ "$compute_cap" != "0.0" ]; then
            case "$compute_cap" in
                8.9) cuda_cores="16384" ;;
                8.6) cuda_cores="10240" ;;
                8.0) cuda_cores="10496" ;;
                7.5) cuda_cores="2560" ;;
                7.0) cuda_cores="5120" ;;
                6.1) cuda_cores="3584" ;;
                *) cuda_cores="0" ;;
            esac
        fi
        local vaapi="false"
        if [ -e "/dev/dri/renderD128" ]; then
            vaapi="true"
        fi
        log_success "NVIDIA GPU $i: $model ($vram MiB, driver $driver)"
        cat <<GPUJSON
{"index":$i,"vendor":"nvidia","model":"$model","driver":"$driver","vram_mb":$vram,"cuda_cores":$cuda_cores,"compute_cap":"$compute_cap","bus_id":"$bus_id","vaapi_support":$vaapi}
GPUJSON
        i=$((i + 1))
    done
}

detect_amd() {
    local gpus=()
    local sysfs_drm="/sys/class/drm"
    local found_amd=false
    for card_dir in "$sysfs_drm"/card*/device; do
        if [ ! -d "$card_dir" ]; then
            continue
        fi
        local vendor
        vendor=$(cat "$card_dir/vendor" 2>/dev/null || echo "")
        if [ "$vendor" != "0x1002" ]; then
            continue
        fi
        found_amd=true
        local model="" vram_kb="0" driver="amdgpu"
        local uevent="$card_dir/uevent"
        if [ -f "$uevent" ]; then
            model=$(grep -i "PCI_ID" "$uevent" 2>/dev/null | cut -d= -f2 || echo "")
        fi
        local vram_file="$card_dir/mem_info_vram_total"
        if [ -f "$vram_file" ]; then
            vram_kb=$(cat "$vram_file" 2>/dev/null || echo "0")
        fi
        local vram_mb=$((vram_kb / 1024))
        if command -v rocm-smi &>/dev/null; then
            local rocm_driver
            rocm_driver=$(rocm-smi --showdriverversion 2>/dev/null | grep -i "driver" | awk '{print $NF}' || echo "unknown")
            [ -n "$rocm_driver" ] && driver="rocm $rocm_driver"
        fi
        local vaapi="false"
        if [ -e "/dev/dri/renderD128" ]; then
            vaapi="true"
        fi
        log_success "AMD GPU: ${model:-Unknown} ($vram_mb MiB, driver $driver)"
        cat <<GPUJSON
{"vendor":"amd","model":"${model:-Unknown}","driver":"$driver","vram_mb":$vram_mb,"cuda_cores":0,"compute_cap":"0.0","bus_id":"","vaapi_support":$vaapi}
GPUJSON
    done
}

detect_intel() {
    local sysfs_drm="/sys/class/drm"
    for card_dir in "$sysfs_drm"/card*/device; do
        if [ ! -d "$card_dir" ]; then
            continue
        fi
        local vendor
        vendor=$(cat "$card_dir/vendor" 2>/dev/null || echo "")
        if [ "$vendor" != "0x8086" ]; then
            continue
        fi
        local model="" driver="i915" vram_mb="0"
        local uevent="$card_dir/uevent"
        if [ -f "$uevent" ]; then
            model=$(grep -i "PCI_ID" "$uevent" 2>/dev/null | cut -d= -f2 || echo "")
        fi
        local vram_file="$card_dir/mem_info_vram_total"
        if [ -f "$vram_file" ]; then
            local vram_kb
            vram_kb=$(cat "$vram_file" 2>/dev/null || echo "0")
            vram_mb=$((vram_kb / 1024))
        fi
        local eu_count="0"
        if [ -d "$card_dir/card0" ]; then
            local gt_dir="$card_dir/card0"
            local freq_max
            freq_max=$(cat "$gt_dir/gt_cur_freq_mhz" 2>/dev/null || echo "0")
        fi
        local vaapi="false"
        if [ -e "/dev/dri/renderD128" ]; then
            vaapi="true"
        fi
        log_success "Intel GPU: ${model:-Unknown} ($vram_mb MiB, driver $driver)"
        cat <<GPUJSON
{"vendor":"intel","model":"${model:-Unknown}","driver":"$driver","vram_mb":$vram_mb,"cuda_cores":0,"compute_cap":"0.0","eu_count":"$eu_count","bus_id":"","vaapi_support":$vaapi}
GPUJSON
    done
}

recommend_settings() {
    local vram_mb="${1:-0}"
    local vendor="${2:-unknown}"
    local zram_ratio="2.0"
    local game_cpu="100"
    local bg_cpu="10"
    if [ "$vram_mb" -ge 16000 ]; then
        zram_ratio="1.0"
        game_cpu="100"
        bg_cpu="10"
    elif [ "$vram_mb" -ge 8000 ]; then
        zram_ratio="1.5"
        game_cpu="100"
        bg_cpu="10"
    elif [ "$vram_mb" -ge 4000 ]; then
        zram_ratio="2.0"
        game_cpu="100"
        bg_cpu="10"
    else
        zram_ratio="2.5"
        game_cpu="100"
        bg_cpu="5"
    fi
    local recom_vo="gpu"
    local recom_hwdec="vaapi"
    case "$vendor" in
        nvidia)
            recom_vo="gpu"
            recom_hwdec="cuda"
            ;;
        amd)
            recom_vo="gpu"
            recom_hwdec="vaapi"
            ;;
        intel)
            recom_vo="gpu"
            recom_hwdec="vaapi"
            ;;
    esac
    cat <<RECOMJSON
"recommended_settings":{"zram_ratio":$zram_ratio,"game_cpu_weight":$game_cpu,"bg_cpu_weight":$bg_cpu,"vo_backend":"$recom_vo","hw_decoding":"$recom_hwdec"}
RECOMJSON
}

detect_all_gpus() {
    local gpu_jsons=()
    local max_vram=0
    local primary_vendor="none"
    while IFS= read -r line; do
        gpu_jsons+=("$line")
        local vram
        vram=$(echo "$line" | grep -o '"vram_mb":[0-9]*' | cut -d: -f2 || echo "0")
        vram=${vram:-0}
        if [ "$vram" -gt "$max_vram" ]; then
            max_vram=$vram
        fi
        local vendor
        vendor=$(echo "$line" | grep -o '"vendor":"[^"]*"' | cut -d'"' -f4 || echo "")
        [ -n "$vendor" ] && primary_vendor="$vendor"
    done < <( {
        detect_nvidia
        detect_amd
        detect_intel
    } 2>/dev/null )
    if [ ${#gpu_jsons[@]} -eq 0 ]; then
        log_warn "No GPU detected — using software rendering defaults"
        primary_vendor="none"
        max_vram=0
    fi
    echo "$primary_vendor" "$max_vram" "${#gpu_jsons[@]}"
}

output_json() {
    local primary_vendor="$1"
    local max_vram="$2"
    local gpu_count="$3"
    local gpu_jsons=()
    while IFS= read -r line; do
        [ -n "$line" ] && gpu_jsons+=("$line")
    done < <( { detect_nvidia; detect_amd; detect_intel; } 2>/dev/null )
    local gpus_array="["
    local first=true
    for gj in "${gpu_jsons[@]}"; do
        [ -z "$gj" ] && continue
        if [ "$first" = true ]; then
            first=false
        else
            gpus_array+=","
        fi
        gpus_array+="$gj"
    done
    gpus_array+="]"
    local settings
    settings=$(recommend_settings "$max_vram" "$primary_vendor")
    cat > "$OUTPUT_FILE" <<EOF
{
  "primary_vendor": "$primary_vendor",
  "max_vram_mb": $max_vram,
  "gpu_count": $gpu_count,
  "gpus": $gpus_array,
  $settings,
  "vaapi_device_exists": $( [ -e "/dev/dri/renderD128" ] && echo "true" || echo "false" ),
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
    log_success "GPU profile written to $OUTPUT_FILE"
}

main() {
    log_info "=== NexusOS GPU Profiler starting ==="
    read -r primary_vendor max_vram gpu_count <<< "$(detect_all_gpus)"
    output_json "$primary_vendor" "$max_vram" "$gpu_count"
    log_info "=== GPU Profiler complete ==="
}

main "$@"
