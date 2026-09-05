#!/usr/bin/env bash
# Linux 프로세스의 CPU 사용률과 메모리 사용량을 한 번 측정하는 스크립트입니다.
#
# 사용법:
#   bash monitor.sh 1234                 # 실제 작업 프로세스의 PID 지정
#   bash monitor.sh                      # 대상 앱이 하나일 때만 자동 선택
#   MONITOR_LOG_FILE=./monitor.log bash monitor.sh 1234
#
# 연속 관측은 호출하는 쪽에서 반복합니다. 한 번 실행할 때 약 1초가 걸립니다.
#   while bash monitor.sh 1234; do :; done
#
# CPU는 약 1초 동안 사용한 CPU 시간으로 계산하고,
# 메모리는 그 구간이 끝난 시점의 RSS를 읽습니다.

# 명령 실패(-e), 미정의 변수 사용(-u), 파이프 중간 실패(pipefail)를 감지합니다.
set -euo pipefail
# 명령 출력의 언어와 소수점 표기를 고정해 일관되게 읽습니다.
export LC_ALL=C

fail() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

# 1. 실행 환경과 로그 저장 위치를 준비합니다.
# /proc는 Linux가 프로세스·시스템 정보를 파일 형태로 제공하는 경로입니다.
[[ -d /proc ]] || fail 'Linux에서 실행하세요.'

# 로그 경로 우선순위:
# MONITOR_LOG_FILE → AGENT_LOG_DIR/monitor.log → ./runtime/logs/monitor.log
# ${변수:-기본값}은 변수가 없거나 비어 있을 때 기본값을 사용합니다.
log_file="${MONITOR_LOG_FILE:-${AGENT_LOG_DIR:-./runtime/logs}/monitor.log}"
pid="${1:-}"

# 2. 측정할 PID를 결정합니다.
# 인자가 없으면 /proc/<PID>/exe가 가리키는 실행 파일 이름으로 앱을 찾습니다.
# 부모 실행기와 작업 자식이 함께 있으면 모호하므로 PID를 직접 지정해야 합니다.
if [[ -z "$pid" ]]; then
    candidates=()
    for entry in /proc/[0-9]*/exe; do
        # 조회하는 사이 프로세스가 종료되거나 권한이 없으면 건너뜁니다.
        executable="$(readlink "$entry" 2>/dev/null)" || continue
        # ##*/는 앞의 디렉터리 경로를 제거하고 파일 이름만 남깁니다.
        case "${executable##*/}" in
            agent-leak-app-x86|agent-leak-app-arm64)
                entry="${entry%/exe}"       # /proc/1234/exe → /proc/1234
                candidates+=("${entry##*/}") # /proc/1234 → 1234
                ;;
        esac
    done
    [[ ${#candidates[@]} -eq 1 ]] || fail '대상 프로세스를 하나로 식별할 수 없습니다. ./monitor.sh PID 로 지정하세요.'
    pid="${candidates[0]}"
fi
[[ "$pid" =~ ^[0-9]+$ ]] || fail 'PID는 양의 정수여야 합니다.'
mkdir -p "$(dirname "$log_file")"

# 3. CPU 측정에 필요한 누적 사용 시간과 프로세스 시작 시각을 읽습니다.
# 출력 형식: <누적 CPU tick> <시스템 부팅 후 프로세스 시작 tick>
# tick은 운영체제가 CPU 사용 시간을 기록할 때 사용하는 단위입니다.
read_cpu_counters() {
    local stat_line
    stat_line="$(cat "/proc/$pid/stat" 2>/dev/null)" || return 1

    # stat의 앞부분은 "PID (프로세스 이름) 상태 ..."입니다.
    # 이름에는 공백과 괄호가 들어갈 수 있어, 마지막 ") "까지 먼저 제거합니다.
    # 제거 후 awk 필드 번호는 원래 번호보다 2 작아집니다.
    #   $12: utime — 사용자 모드 CPU tick (원래 14번)
    #   $13: stime — 커널 모드 CPU tick   (원래 15번)
    #   $20: starttime — 시작 tick       (원래 22번)
    printf '%s\n' "${stat_line##*) }" | awk '{print $12+$13, $20}'
}

# 측정 중 프로세스가 사라지거나 정보를 읽지 못하면 상태를 남기고 종료합니다.
# 이 기록만으로 앱의 종료 원인이나 정상 종료 여부를 확정하지는 않습니다.
record_exit() {
    printf '[%s] PID:%s STATUS:EXITED_OR_UNREADABLE\n' "$(date -Iseconds)" "$pid" | tee -a "$log_file"
    exit 1
}

# 4. 약 1초 간격으로 두 번 읽어 해당 구간의 CPU 사용률을 계산합니다.
# read는 공백으로 나뉜 두 값을 각 변수에 넣습니다.
# <<<는 문자열을 표준 입력으로 전달하고, -r은 역슬래시 해석을 막습니다.
before="$(read_cpu_counters)" || record_exit
read -r ticks_before start_before <<< "$before"
# /proc/uptime의 첫 번째 값은 시스템 부팅 이후 경과한 초입니다.
uptime_before="$(awk '{print $1}' /proc/uptime)"

sleep 1

after="$(read_cpu_counters)" || record_exit
read -r ticks_after start_after <<< "$after"
uptime_after="$(awk '{print $1}' /proc/uptime)"

# 종료된 프로세스의 PID가 다른 프로세스에 재사용될 수 있습니다.
# 두 번 읽은 시작 시각이 같아야 같은 프로세스의 CPU 시간을 비교할 수 있습니다.
[[ "$start_before" = "$start_after" ]] || fail 'PID가 재사용되었습니다. 다시 대상을 확인하세요.'
ticks_per_second="$(getconf CLK_TCK)"

# CPU 사용률 = (누적 tick 증가량 / 초당 tick 수) / 실제 경과 초 × 100
# 코어 하나를 계속 사용하면 100%입니다. 여러 코어를 쓰면 100%를 넘을 수 있습니다.
# sleep 1이 정확히 1초를 보장하지 않으므로 실제 경과 시간을 사용합니다.
cpu="$(awk \
    -v ticks_before="$ticks_before" \
    -v ticks_after="$ticks_after" \
    -v ticks_per_second="$ticks_per_second" \
    -v time_before="$uptime_before" \
    -v time_after="$uptime_after" \
    'BEGIN {
        elapsed_seconds = time_after - time_before
        if (elapsed_seconds <= 0) exit 1
        cpu_seconds = (ticks_after - ticks_before) / ticks_per_second
        printf "%.1f", cpu_seconds / elapsed_seconds * 100
    }')"

# 5. ps로 현재 메모리 사용량과 프로세스 상태를 읽습니다.
# -p: 대상 PID 지정, -o: 출력할 열 선택, 열 이름 뒤의 =: 제목 행 생략
# rss: RAM에 상주하는 페이지의 양(KiB). 앱의 Heap 카운터와는 다릅니다.
# %mem: Linux가 보는 전체 메모리 대비 RSS 비율. 앱 메모리 한도 대비 비율이 아닙니다.
# stat: 프로세스 상태. 예를 들어 S는 대기, R은 실행 중 또는 실행 가능한 상태입니다.
snapshot="$(ps -p "$pid" -o rss= -o %mem= -o stat=)" || record_exit
# 예: "95324 0.5 SN" → rss_kb=95324, mem_percent=0.5, state=SN
read -r rss_kb mem_percent state <<< "$snapshot"
[[ -n "${rss_kb:-}" ]] || record_exit

# 6. 시각·PID·측정값을 한 줄로 기록합니다. 기존 분석 도구의 출력 형식을 유지합니다.
# date -Iseconds: 시간대가 포함된 시각. tee -a: 화면 출력과 파일 끝에 추가 저장.
# 반복 수집한 RSS_KB를 시간순으로 비교하면 메모리 증가·정리 패턴을 볼 수 있습니다.
printf '[%s] PID:%s CPU:%s%% RSS_KB:%s MEM:%s%% STATE:%s\n' \
    "$(date -Iseconds)" "$pid" "$cpu" "$rss_kb" "$mem_percent" "$state" | tee -a "$log_file"
