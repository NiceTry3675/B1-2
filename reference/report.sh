#!/usr/bin/env bash

set -u
set -o pipefail

readonly LOG_FILE="${1:-${MONITOR_LOG_FILE:-/var/log/agent-app/monitor.log}}"

if [[ ! -e "$LOG_FILE" ]]; then
    printf '[ERROR] Log file does not exist: %s\n' "$LOG_FILE" >&2
    exit 1
fi

if [[ ! -r "$LOG_FILE" ]]; then
    printf '[ERROR] Log file is not readable: %s\n' "$LOG_FILE" >&2
    exit 1
fi

awk '
function numeric(value) {
    return value ~ /^[0-9]+([.][0-9]+)?$/
}

function update(metric, value, timestamp) {
    sum[metric] += value
    if (!(metric in min) || value < min[metric]) {
        min[metric] = value
        min_time[metric] = timestamp
    }
    if (!(metric in max) || value > max[metric]) {
        max[metric] = value
        max_time[metric] = timestamp
    }
}

{
    if (NF != 6 || $1 !~ /^\[[0-9]{4}-[0-9]{2}-[0-9]{2}$/ ||
        $2 !~ /^[0-9]{2}:[0-9]{2}:[0-9]{2}\]$/ ||
        $3 !~ /^PID:[0-9]+$/ || $4 !~ /^CPU:/ ||
        $5 !~ /^MEM:/ || $6 !~ /^DISK_USED:/) {
        invalid++
        next
    }

    timestamp = substr($1, 2) " " substr($2, 1, length($2) - 1)
    cpu = $4
    mem = $5
    disk = $6
    sub(/^CPU:/, "", cpu)
    sub(/%$/, "", cpu)
    sub(/^MEM:/, "", mem)
    sub(/%$/, "", mem)
    sub(/^DISK_USED:/, "", disk)
    sub(/%$/, "", disk)

    if (!numeric(cpu) || !numeric(mem) || !numeric(disk)) {
        invalid++
        next
    }

    count++
    update("CPU", cpu + 0, timestamp)
    update("MEM", mem + 0, timestamp)
    update("DISK", disk + 0, timestamp)
}

END {
    if (count == 0) {
        print "[ERROR] No valid monitor samples were found." > "/dev/stderr"
        exit 2
    }

    print "====== STATISTICS REPORT ======"
    print ""
    for (idx = 1; idx <= 3; idx++) {
        metric = (idx == 1 ? "CPU" : (idx == 2 ? "MEM" : "DISK"))
        print "[" metric "]"
        printf "Average : %.1f%%\n", sum[metric] / count
        printf "Maximum : %.1f%% at %s\n", max[metric], max_time[metric]
        printf "Minimum : %.1f%% at %s\n", min[metric], min_time[metric]
        print ""
    }
    print "[Samples]"
    printf "Data Points: %d samples\n", count
    if (invalid > 0) {
        printf "[WARNING] Ignored %d malformed line(s).\n", invalid > "/dev/stderr"
    }
}
' "$LOG_FILE"
