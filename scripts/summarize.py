#!/usr/bin/env python3
"""Derive numerical report data from the preserved evidence, without editing it."""
import csv
import datetime as dt
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
CASES = ['oom/before', 'oom/after', 'cpu/before-02', 'cpu/after', 'deadlock/before', 'deadlock/after']
PATTERN = re.compile(r'\[(.*?)\] PID:(\d+) CPU:([\d.]+)% RSS_KB:(\d+) MEM:([\d.]+)% STATE:(\S+)')


def read_case(case):
    folder = ROOT / 'evidence' / case
    meta = json.loads((folder / 'result.json').read_text())
    # This binary launches one child. Preserve both logs, analyze the workload.
    pid = next(p for p in meta['observed_pids'] if p != meta['launcher_pid'])
    rows = []
    for match in PATTERN.finditer((folder / f'monitor-{pid}.log').read_text()):
        stamp, _, cpu, rss, mem, state = match.groups()
        if state.startswith('Z'):
            continue
        rows.append(dict(timestamp=stamp, cpu=float(cpu), rss_kb=int(rss), mem=float(mem), state=state))
    cpu_values = [row['cpu'] for row in rows]
    rss_values = [row['rss_kb'] for row in rows]
    info = dict(case=case, pid=pid, **meta, sample_count=len(rows),
                first_rss_kb=rss_values[0], last_rss_kb=rss_values[-1],
                min_rss_kb=min(rss_values), max_rss_kb=max(rss_values),
                max_cpu=max(cpu_values), mean_cpu=round(statistics.mean(cpu_values), 3),
                first_sample=rows[0]['timestamp'], last_sample=rows[-1]['timestamp'])
    burst = folder / f'cpu-burst-{pid}.csv'
    if burst.exists():
        samples = list(csv.DictReader(burst.open()))
        peak = max(samples, key=lambda r: float(r['cpu_percent']))
        info['burst_peak'] = peak
    return info, rows


def main():
    infos = []
    for case in CASES:
        info, rows = read_case(case)
        infos.append(info)
    (ROOT / 'evidence/summary.json').write_text(json.dumps(infos, indent=2) + '\n')
    print(json.dumps(infos, indent=2))


if __name__ == '__main__':
    main()
