#!/bin/bash
set -euo pipefail
# Aion CPU-Aware Scheduler — Per-core tuning, cgroups, and governor management

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

LOG_FILE="/var/log/aion/cpu-scheduler.log"
CGROUP_BASE="/sys/fs/cgroup"
AION_GAMING_CGROUP="${CGROUP_BASE}/aion-gaming"
AION_BG_CGROUP="${CGROUP_BASE}/aion-background"

log_info()    { echo -e "${CYAN}[INFO]${NC} $(date '+%H:%M:%S') $*"; echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }
log_success() { echo -e "${GREEN}[OK]${NC} $(date '+%H:%M:%S') $*"; echo "[OK] $(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"; }

mkdir -p "$(dirname "$LOG_FILE")"

detect_cpu_info() {
    local total_cores sockets threads_per_core cpu_vendor

    total_cores=$(nproc 2>/dev/null || echo "1")
    sockets=$(grep -c "^physical id" /proc/cpuinfo 2>/dev/null || echo "1")
    sockets=$((sockets > 0 ? sockets : 1))
    threads_per_core=$((total_cores / sockets))
    local physical_cores_per_socket
    physical_cores_per_socket=$(grep -c "^cpu cores" /proc/cpuinfo 2>/dev/null || echo "$total_cores")
    physical_cores_per_socket=$((physical_cores_per_socket / sockets))
    threads_per_core=$((total_cores / (sockets * physical_cores_per_socket)))
    [ "$threads_per_core" -lt 1 ] && threads_per_core=1

    if grep -qi "authenticamd" /proc/cpuinfo 2>/dev/null; then
        cpu_vendor="amd"
    elif grep -qi "genuineintel" /proc/cpuinfo 2>/dev/null; then
        cpu_vendor="intel"
    else
        cpu_vendor="unknown"
    fi

    export TOTAL_CORES=$total_cores
    export SOCKETS=$sockets
    export THREADS_PER_CORE=$threads_per_core
    export CPU_VENDOR=$cpu_vendor

    log_info "CPU: $cpu_vendor | $sockets socket(s) | $total_cores cores | $threads_per_core threads/core"
}

configure_pstate() {
    case "$CPU_VENDOR" in
        amd)
            if [ -d "/sys/devices/system/cpu/cpufreq/policy0" ]; then
                local driver
                driver=$(cat "/sys/devices/system/cpu/cpufreq/policy0/scaling_driver" 2>/dev/null || echo "")
                if [ "$driver" = "amd-pstate" ] || [ "$driver" = "amd-pstate-epp" ]; then
                    log_info "AMD P-State already active ($driver)"
                    for policy_dir in /sys/devices/system/cpu/cpufreq/policy*; do
                        echo balance_performance > "${policy_dir}/energy_performance_preference" 2>/dev/null || true
                    done
                    log_success "Set AMD P-State to balance_performance"
                else
                    log_warn "AMD P-State not active (driver: ${driver:-none}) — set kernel param amd_pstate=active"
                fi
            fi
            ;;
        intel)
            if [ -d "/sys/devices/system/cpu/cpufreq/policy0" ]; then
                local driver
                driver=$(cat "/sys/devices/system/cpu/cpufreq/policy0/scaling_driver" 2>/dev/null || echo "")
                if [ "$driver" = "intel_pstate" ]; then
                    log_info "Intel P-State already active"
                    for policy_dir in /sys/devices/system/cpu/cpufreq/policy*; do
                        echo balance_performance > "${policy_dir}/energy_performance_preference" 2>/dev/null || true
                    done
                    log_success "Set Intel P-State to balance_performance"
                else
                    log_info "Intel P-State not active — using acpi-cpufreq"
                fi
            fi
            ;;
        *)
            log_info "Unknown CPU vendor — skipping P-State configuration"
            ;;
    esac
}

setup_cgroups_v2() {
    log_info "Setting up cgroups v2"

    if ! mountpoint -q "$CGROUP_BASE" 2>/dev/null; then
        log_warn "cgroups v2 not mounted at $CGROUP_BASE"
        return 1
    fi

    local game_weight bg_weight
    if [ "$TOTAL_CORES" -le 4 ]; then
        game_weight=100
        bg_weight=5
    elif [ "$TOTAL_CORES" -le 8 ]; then
        game_weight=100
        bg_weight=10
    elif [ "$TOTAL_CORES" -le 16 ]; then
        game_weight=100
        bg_weight=15
    else
        game_weight=100
        bg_weight=25
    fi

    export GAME_WEIGHT=$game_weight
    export BG_WEIGHT=$bg_weight

    mkdir -p "$AION_GAMING_CGROUP" "$AION_BG_CGROUP"

    echo "$game_weight" > "${AION_GAMING_CGROUP}/cpu.weight" 2>/dev/null || true
    echo "$bg_weight" > "${AION_BG_CGROUP}/cpu.weight" 2>/dev/null || true

    echo "+cpu +cpuset" > "${AION_GAMING_CGROUP}/cgroup.subtree_control" 2>/dev/null || true
    echo "+cpu +cpuset" > "${AION_BG_CGROUP}/cgroup.subtree_control" 2>/dev/null || true

    if [ "$TOTAL_CORES" -ge 8 ]; then
        local game_cpus=""
        local bg_cpus=""
        local core_idx=0
        local half_cores=$((TOTAL_CORES / 2))

        while [ "$core_idx" -lt "$TOTAL_CORES" ]; do
            if [ "$core_idx" -lt "$half_cores" ]; then
                [ -n "$game_cpus" ] && game_cpus="${game_cpus},${core_idx}" || game_cpus="${core_idx}"
            else
                [ -n "$bg_cpus" ] && bg_cpus="${bg_cpus},${core_idx}" || bg_cpus="${core_idx}"
            fi
            core_idx=$((core_idx + 1))
        done

        echo "$game_cpus" > "${AION_GAMING_CGROUP}/cpuset.cpus" 2>/dev/null || true
        echo "0-$((TOTAL_CORES - 1))" > "${AION_GAMING_CGROUP}/cpuset.cpus.partition" 2>/dev/null || true
        [ -n "$bg_cpus" ] && echo "$bg_cpus" > "${AION_BG_CGROUP}/cpuset.cpus" 2>/dev/null || true
        echo "0-$((TOTAL_CORES - 1))" > "${AION_BG_CGROUP}/cpuset.cpus.partition" 2>/dev/null || true
    fi

    log_success "cgroups created: gaming(weight=$game_weight) background(weight=$bg_weight)"
}

write_ananicy_rules() {
    local rules_dir="/etc/ananicy.d"
    mkdir -p "$rules_dir"

    cat > "${rules_dir}/00-aion-gaming.conf" <<'EOF'
# Aion Ananicy-cpp rules — gaming process priority
# Gaming engines and launchers
type=Process: label=game: nice=-10: cgroup=aion-gaming
type=Process: label=game-engine: nice=-15: cgroup=aion-gaming

# Steam
type=Name: name=steam: nice=-10: cgroup=aion-gaming
type=Name: name=steamwebhelper: nice=-5: cgroup=aion-gaming
type=Name: name=steam_oOo...*: nice=-10: cgroup=aion-gaming

# Wine/Proton
type=Name: name=wine*: nice=-10: cgroup=aion-gaming
type=Name: name=explorer.exe: nice=-5: cgroup=aion-gaming
type=Name: name=services.exe: nice=-15: cgroup=aion-gaming
type=Name: name=plugplay.exe: nice=-15: cgroup=aion-gaming
type=Name: name=rpcss.exe: nice=-5: cgroup=aion-gaming
type=Name: name=GamemodeThread: nice=-15: cgroup=aion-gaming

# Lutris
type=Name: name=lutris: nice=-10: cgroup=aion-gaming
type=Name: name=lutris-wrapper: nice=-10: cgroup=aion-gaming

# OBS (game capture)
type=Name: name=obs: nice=-5: cgroup=aion-gaming

# Background processes — deprioritize
type=Name: name=firefox: nice=5: cgroup=aion-background
type=Name: name=chromium: nice=5: cgroup=aion-background
type=Name: name=discord: nice=3: cgroup=aion-background
type=Name: name=spotify: nice=5: cgroup=aion-background
type=Name: name=code: nice=5: cgroup=aion-background
type=Name: name=slack: nice=5: cgroup=aion-background
type=Name: name=thunderbird: nice=5: cgroup=aion-background

# System overhead — lowest priority
type=Name: name=PackageKit: nice=19: cgroup=aion-background
type=Name: name=tracker-miner: nice=19: cgroup=aion-background
type=Name: name=baloo_file: nice=19: cgroup=aion-background
EOF

    log_success "Ananicy-cpp rules written to ${rules_dir}/00-aion-gaming.conf"
}

set_governor() {
    local governor="$1"
    if [ -d "/sys/devices/system/cpu/cpufreq/policy0" ]; then
        for policy_dir in /sys/devices/system/cpu/cpufreq/policy*; do
            echo "$governor" > "${policy_dir}/scaling_governor" 2>/dev/null || {
                log_warn "Could not set governor to $governor on $(basename "$policy_dir")"
            }
        done
        log_success "CPU governor set to: $governor"
    fi
}

gaming_start() {
    log_info "=== Gaming mode STARTED ==="
    set_governor "performance"

    echo "$GAME_WEIGHT" > "${AION_GAMING_CGROUP}/cpu.weight" 2>/dev/null || true
    echo "$BG_WEIGHT" > "${AION_BG_CGROUP}/cpu.weight" 2>/dev/null || true

    for policy_dir in /sys/devices/system/cpu/cpufreq/policy*; do
        echo performance > "${policy_dir}/energy_performance_preference" 2>/dev/null || true
    done

    log_success "CPU tuned for gaming (governor=performance, gaming_weight=$GAME_WEIGHT, bg_weight=$BG_WEIGHT)"
}

gaming_stop() {
    log_info "=== Gaming mode STOPPED ==="
    set_governor "schedutil"

    echo "$GAME_WEIGHT" > "${AION_GAMING_CGROUP}/cpu.weight" 2>/dev/null || true
    echo "$BG_WEIGHT" > "${AION_BG_CGROUP}/cpu.weight" 2>/dev/null || true

    for policy_dir in /sys/devices/system/cpu/cpufreq/policy*; do
        echo balance_performance > "${policy_dir}/energy_performance_preference" 2>/dev/null || true
    done

    log_success "CPU restored to balanced (governor=schedutil)"
}

show_usage() {
    cat <<EOF
Aion CPU-Aware Scheduler

Usage: $(basename "$0") <command>

Commands:
    --setup         Initialize cgroups, pstate, ananicy rules
    --gaming-start  Switch to gaming mode (performance governor, high priority)
    --gaming-stop   Restore balanced mode (schedutil governor)
    --info          Show detected CPU information
    --help          Show this help

Detected: ${TOTAL_CORES:-?} cores | ${CPU_VENDOR:-?} vendor | ${SOCKETS:-?} socket(s)
EOF
}

main() {
    local cmd="${1:---help}"
    detect_cpu_info

    case "$cmd" in
        --setup)
            log_info "=== CPU Scheduler Setup ==="
            configure_pstate
            setup_cgroups_v2
            write_ananicy_rules
            set_governor "schedutil"
            log_success "=== CPU Scheduler Setup Complete ==="
            ;;
        --gaming-start)
            gaming_start
            ;;
        --gaming-stop)
            gaming_stop
            ;;
        --info)
            echo "CPU Vendor:    $CPU_VENDOR"
            echo "Total Cores:   $TOTAL_CORES"
            echo "Sockets:       $SOCKETS"
            echo "Threads/Core:  $THREADS_PER_CORE"
            echo "Game Weight:   ${GAME_WEIGHT:-100}"
            echo "BG Weight:     ${BG_WEIGHT:-10}"
            ;;
        --help|-h|*)
            show_usage
            ;;
    esac
}

main "$@"
