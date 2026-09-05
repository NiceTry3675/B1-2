#!/usr/bin/env python3
"""50 ms /proc CPU samples to expose bursts hidden by 1-second averages."""
import datetime as dt
import os
from pathlib import Path
import sys
import time

pid = int(sys.argv[1])
seconds = float(sys.argv[2])
hz = os.sysconf('SC_CLK_TCK')
deadline = time.monotonic() + seconds
last = None
print('timestamp,pid,cpu_percent,interval_seconds,state,rss_kb', flush=True)
while time.monotonic() < deadline:
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
    except FileNotFoundError:
        break
    ticks = int(fields[11]) + int(fields[12])
    stamp = time.monotonic()
    identity = fields[19]
    if fields[0] == 'Z':
        break
    if last:
        old_stamp, old_ticks, old_id = last
        if identity != old_id:
            raise RuntimeError('PID reused')
        span = stamp - old_stamp
        cpu = (ticks - old_ticks) / hz / span * 100
        rss = int(fields[21]) * os.sysconf('SC_PAGE_SIZE') // 1024
        print(f'{dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")},{pid},{cpu:.1f},{span:.6f},{fields[0]},{rss}', flush=True)
    last = stamp, ticks, identity
    time.sleep(0.05)
