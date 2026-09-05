#!/usr/bin/env bash
# Host-side entry point. Optional suffix creates fresh evidence without overwriting.
set -euo pipefail
cd "$(dirname "$0")/.."
docker_bin="${DOCKER_BIN:-docker}"
suffix="${1:-}"
"$docker_bin" build -t b1-2-lab .
while read -r case_name memory cpu multi seconds; do
    "$docker_bin" run --rm --init --memory=1g --cpus=2 --network=none \
        -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py "${case_name}${suffix}" \
        --memory "$memory" --cpu "$cpu" --multi "$multi" --seconds "$seconds"
done <<'CASES'
oom/before 100 40 false 90
oom/after 200 40 false 90
cpu/before-02 512 100 false 100
cpu/after 512 40 false 75
deadlock/before 512 40 true 60
deadlock/after 512 40 false 60
CASES
