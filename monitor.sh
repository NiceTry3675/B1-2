#!/usr/bin/env bash
# Linux 전용. 1초 간격 프로세스 CPU와 RSS를 한 번 수집합니다.
set -euo pipefail
export LC_ALL=C

fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
[[ -d /proc ]] || fail 'Linux에서 실행하세요.'
log_file="${MONITOR_LOG_FILE:-${AGENT_LOG_DIR:-./runtime/logs}/monitor.log}"
pid="${1:-}"
if [[ -z "$pid" ]]; then
    candidates=()
    for entry in /proc/[0-9]*/exe; do
        executable="$(readlink "$entry" 2>/dev/null)" || continue
        case "${executable##*/}" in
            agent-leak-app-x86|agent-leak-app-arm64)
                entry="${entry%/exe}"
                candidates+=("${entry##*/}") ;;
        esac
    done
    [[ ${#candidates[@]} -eq 1 ]] || fail '대상 프로세스를 하나로 식별할 수 없습니다. ./monitor.sh PID 로 지정하세요.'
    pid="${candidates[0]}"
fi
[[ "$pid" =~ ^[0-9]+$ ]] || fail 'PID는 양의 정수여야 합니다.'
mkdir -p "$(dirname "$log_file")"

# comm 필드에는 공백과 괄호가 들어갈 수 있어 마지막 ") "까지 제거합니다.
counters() {
    local stat_line
    stat_line="$(cat "/proc/$pid/stat" 2>/dev/null)" || return 1
    printf '%s\n' "${stat_line##*) }" | awk '{print $12+$13, $20}'
}
record_exit() {
    printf '[%s] PID:%s STATUS:EXITED_OR_UNREADABLE\n' "$(date -Iseconds)" "$pid" | tee -a "$log_file"
    exit 1
}

before="$(counters)" || record_exit
read -r ticks_before start_before <<< "$before"
uptime_before="$(awk '{print $1}' /proc/uptime)"
sleep 1
after="$(counters)" || record_exit
read -r ticks_after start_after <<< "$after"
[[ "$start_before" = "$start_after" ]] || fail 'PID가 재사용되었습니다. 다시 대상을 확인하세요.'
uptime_after="$(awk '{print $1}' /proc/uptime)"
hz="$(getconf CLK_TCK)"
cpu="$(awk -v a="$ticks_before" -v b="$ticks_after" -v t0="$uptime_before" -v t1="$uptime_after" -v hz="$hz" 'BEGIN {if (t1 <= t0) exit 1; printf "%.1f", (b-a)/hz/(t1-t0)*100}')"
snapshot="$(ps -p "$pid" -o rss= -o %mem= -o stat=)" || record_exit
read -r rss_kb mem_percent state <<< "$snapshot"
[[ -n "${rss_kb:-}" ]] || record_exit
printf '[%s] PID:%s CPU:%s%% RSS_KB:%s MEM:%s%% STATE:%s\n' \
    "$(date -Iseconds)" "$pid" "$cpu" "$rss_kb" "$mem_percent" "$state" | tee -a "$log_file"
