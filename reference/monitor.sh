#!/usr/bin/env bash

set -u
set -o pipefail

readonly AGENT_PORT="${AGENT_PORT:-15034}"
readonly LOG_DIR="${AGENT_LOG_DIR:-/var/log/agent-app}"
readonly LOG_FILE="${MONITOR_LOG_FILE:-${LOG_DIR}/monitor.log}"
readonly LOCK_FILE="${MONITOR_LOCK_FILE:-${LOG_DIR}/.monitor.lock}"
readonly MAX_LOG_BYTES="${MONITOR_MAX_LOG_BYTES:-10485760}"
readonly CPU_THRESHOLD="${CPU_THRESHOLD:-20}"
readonly MEM_THRESHOLD="${MEM_THRESHOLD:-10}"
readonly DISK_THRESHOLD="${DISK_THRESHOLD:-80}"

umask 0007

error_exit() {
    printf '[ERROR] %s\n' "$1" >&2
    exit 1
}

is_number() {
    [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

validate_configuration() {
    [[ "$AGENT_PORT" =~ ^[0-9]+$ ]] || error_exit "AGENT_PORT must be an integer."
    (( AGENT_PORT >= 1 && AGENT_PORT <= 65535 )) || error_exit "AGENT_PORT is out of range."
    [[ "$MAX_LOG_BYTES" =~ ^[0-9]+$ ]] || error_exit "MONITOR_MAX_LOG_BYTES must be an integer."
    (( MAX_LOG_BYTES > 0 )) || error_exit "MONITOR_MAX_LOG_BYTES must be greater than zero."

    local threshold
    for threshold in "$CPU_THRESHOLD" "$MEM_THRESHOLD" "$DISK_THRESHOLD"; do
        is_number "$threshold" || error_exit "Resource thresholds must be non-negative numbers."
    done
}

find_agent_pid() {
    local proc_dir executable basename

    for proc_dir in /proc/[0-9]*; do
        executable="$(readlink "${proc_dir}/exe" 2>/dev/null)" || continue
        basename="${executable##*/}"
        case "$basename" in
            agent-app-linux-x86|agent-app-linux-arm64)
                printf '%s\n' "${proc_dir##*/}"
                return 0
                ;;
        esac
    done

    return 1
}

is_port_listening() {
    command -v ss >/dev/null 2>&1 || error_exit "The ss command is not installed."
    ss -H -ltn 2>/dev/null | awk -v port=":${AGENT_PORT}" '
        $4 ~ (port "$") { found = 1 }
        END { exit(found ? 0 : 1) }
    '
}

check_firewall() {
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet ufw 2>/dev/null; then
        printf 'Firewall (UFW)    : [OK] active\n'
    elif [[ -r /etc/ufw/ufw.conf ]] && grep -Eq '^[[:space:]]*ENABLED=yes([[:space:]]*)$' /etc/ufw/ufw.conf; then
        printf 'Firewall (UFW)    : [OK] enabled\n'
    else
        printf '[WARNING] UFW is inactive or its state could not be verified.\n'
    fi
}

read_cpu_counters() {
    local -a cpu_fields
    read -r -a cpu_fields < /proc/stat
    CPU_IDLE=$((cpu_fields[4] + cpu_fields[5]))
    CPU_TOTAL=$((cpu_fields[1] + cpu_fields[2] + cpu_fields[3] + cpu_fields[4] + cpu_fields[5] + cpu_fields[6] + cpu_fields[7] + cpu_fields[8]))
}

collect_cpu_usage() {
    local first_idle first_total delta_idle delta_total

    [[ -r /proc/stat ]] || error_exit "Cannot read /proc/stat."
    read_cpu_counters
    first_idle=$CPU_IDLE
    first_total=$CPU_TOTAL
    sleep 1
    read_cpu_counters

    delta_idle=$((CPU_IDLE - first_idle))
    delta_total=$((CPU_TOTAL - first_total))
    (( delta_total > 0 )) || error_exit "CPU counters did not advance."

    awk -v idle="$delta_idle" -v total="$delta_total" 'BEGIN { printf "%.1f", (total-idle)*100/total }'
}

collect_mem_usage() {
    local total available

    [[ -r /proc/meminfo ]] || error_exit "Cannot read /proc/meminfo."
    total="$(awk '$1 == "MemTotal:" { print $2 }' /proc/meminfo)"
    available="$(awk '$1 == "MemAvailable:" { print $2 }' /proc/meminfo)"
    [[ "$total" =~ ^[0-9]+$ && "$available" =~ ^[0-9]+$ && "$total" -gt 0 ]] \
        || error_exit "Could not parse memory counters."

    awk -v total="$total" -v available="$available" 'BEGIN { printf "%.1f", (total-available)*100/total }'
}

collect_disk_usage() {
    local used

    used="$(df -P / 2>/dev/null | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')"
    [[ "$used" =~ ^[0-9]+$ ]] || error_exit "Could not determine root filesystem usage."
    printf '%s' "$used"
}

exceeds_threshold() {
    awk -v value="$1" -v threshold="$2" 'BEGIN { exit(value > threshold ? 0 : 1) }'
}

rotate_log_if_needed() {
    local size index

    [[ -e "$LOG_FILE" ]] || return 0
    size="$(stat -c '%s' "$LOG_FILE" 2>/dev/null)" || error_exit "Cannot inspect ${LOG_FILE}."
    (( size >= MAX_LOG_BYTES )) || return 0

    rm -f "${LOG_FILE}.9"
    for ((index = 8; index >= 1; index--)); do
        if [[ -e "${LOG_FILE}.${index}" ]]; then
            mv "${LOG_FILE}.${index}" "${LOG_FILE}.$((index + 1))" \
                || error_exit "Failed to rotate ${LOG_FILE}.${index}."
        fi
    done
    mv "$LOG_FILE" "${LOG_FILE}.1" || error_exit "Failed to rotate ${LOG_FILE}."
}

write_log() {
    local timestamp="$1" pid="$2" cpu="$3" mem="$4" disk="$5"

    [[ -d "$LOG_DIR" ]] || error_exit "Log directory does not exist: ${LOG_DIR}"
    [[ -w "$LOG_DIR" ]] || error_exit "Log directory is not writable: ${LOG_DIR}"

    exec 9>"$LOCK_FILE" || error_exit "Cannot open monitor lock: ${LOCK_FILE}"
    if ! flock -n 9; then
        printf '[WARNING] Another monitor instance is writing the log; skipping this sample.\n'
        return 0
    fi

    rotate_log_if_needed
    printf '[%s] PID:%s CPU:%s%% MEM:%s%% DISK_USED:%s%%\n' \
        "$timestamp" "$pid" "$cpu" "$mem" "$disk" >> "$LOG_FILE" \
        || error_exit "Failed to append to ${LOG_FILE}."
    printf 'Log File           : %s\n' "$LOG_FILE"
}

main() {
    local pid cpu_usage mem_usage disk_usage timestamp

    validate_configuration

    printf '====== SYSTEM MONITOR RESULT ======\n\n'
    printf '[HEALTH CHECK]\n'
    pid="$(find_agent_pid)" || error_exit "Agent application process is not running."
    printf "Checking agent process... [OK] (PID: %s)\n" "$pid"

    is_port_listening || error_exit "TCP port ${AGENT_PORT} is not listening."
    printf 'Checking port %s... [OK]\n' "$AGENT_PORT"

    printf '\n[STATUS CHECK]\n'
    check_firewall

    printf '\n[RESOURCE MONITORING]\n'
    cpu_usage="$(collect_cpu_usage)"
    mem_usage="$(collect_mem_usage)"
    disk_usage="$(collect_disk_usage)"
    printf 'CPU Usage          : %s%%\n' "$cpu_usage"
    printf 'MEM Usage          : %s%%\n' "$mem_usage"
    printf 'DISK Used          : %s%%\n' "$disk_usage"

    printf '\n[THRESHOLD CHECK]\n'
    if exceeds_threshold "$cpu_usage" "$CPU_THRESHOLD"; then
        printf '[WARNING] CPU threshold exceeded (%s%% > %s%%)\n' "$cpu_usage" "$CPU_THRESHOLD"
    fi
    if exceeds_threshold "$mem_usage" "$MEM_THRESHOLD"; then
        printf '[WARNING] MEM threshold exceeded (%s%% > %s%%)\n' "$mem_usage" "$MEM_THRESHOLD"
    fi
    if exceeds_threshold "$disk_usage" "$DISK_THRESHOLD"; then
        printf '[WARNING] DISK_USED threshold exceeded (%s%% > %s%%)\n' "$disk_usage" "$DISK_THRESHOLD"
    fi

    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    printf '\n[LOGGING]\n'
    write_log "$timestamp" "$pid" "$cpu_usage" "$mem_usage" "$disk_usage"
    printf '\n====== MONITOR COMPLETE ======\n'
}

main "$@"
