#!/usr/bin/env python3
"""Validate the additional live stack evidence for evaluation item #15."""
import datetime as dt
import json
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'evidence/deadlock/diagnostic-01'
meta = json.loads((folder / 'result.json').read_text())
assert hashlib.sha256((ROOT/'agent-app-leak/agent-leak-app-x86').read_bytes()).hexdigest() == '7e0a19cfa80ece6b547a5008273661f0d4d71e526e96b51e0d0f341dd1bb3e40'
assert meta['settings']['MULTI_THREAD_ENABLE'] == 'true'
assert meta['observer_stopped'] and meta['elapsed_seconds'] >= 60
assert 'uid=1000(analyst)' in (folder / 'preflight.txt').read_text()
trace = (folder / 'strace-futex-write.txt').read_text()
stacks = [(folder / f'gdb-thread-bt-{n:02}.txt').read_text() for n in [1, 2]]
procs = [(folder / f'proc-after-gdb-{n:02}.txt').read_text() for n in [1, 2]]
times = [dt.datetime.fromisoformat(t.splitlines()[0].removeprefix('UTC ')) for t in stacks]
assert (times[1]-times[0]).total_seconds() >= 15
for worker, wanted, held in [(1, 'Socket_Pool_B', 'Shared_Memory_A'), (2, 'Shared_Memory_A', 'Socket_Pool_B')]:
    match = re.search(rf'^(\d+)\s+.*write\(1, .*Worker-Thread-{worker}.*WAITING for \[{wanted}\]', trace, re.M)
    assert match, worker
    tid = match.group(1)
    assert re.search(rf'^{tid}\s+.*write\(1, .*Worker-Thread-{worker}.*LOCK ACQUIRED: \[{held}\]', trace, re.M)
    calls=[]
    for text in procs:
        calls.append(re.search(rf'/task/{tid}/syscall\n([^\n]+)', text).group(1))
        assert re.search(rf'/task/{tid}/wchan\n__futex_wait', text)
    assert calls[0] == calls[1], tid
    assert calls[0].split()[0] == '202'
    addr = calls[0].split()[1]
    assert re.search(rf'^{tid}\s+.*futex\({addr}, FUTEX_WAIT_BITSET_PRIVATE\|FUTEX_CLOCK_REALTIME, 0, NULL', trace, re.M)
    blocks=[]
    for stack in stacks:
        block = re.search(rf'Thread \d+ \(Thread [^\n]+\(LWP {tid}\)[^\n]*\n(.*?)(?=\nThread \d+|\n\[Inferior)', stack, re.S).group(1)
        assert 'PyThread_acquire_lock_timed' in block
        blocks.append(block)
        assert 'detached]' in stack
    assert blocks[0] == blocks[1], tid
    print(f'PASS Worker-{worker} -> LWP {tid} -> futex {addr}; identical lock-acquire stacks and post-detach syscalls')
sizes=[re.search(r'app.log bytes=(\d+)', p).group(1) for p in procs]
assert sizes[0] == sizes[1] == str((folder / 'app.log').stat().st_size)
raw='\n'.join(p.read_text() for p in folder.glob('*.txt'))
count=0
for block in re.findall(r'```text\n(.*?)\n```', (ROOT/'reports/deadlock.md').read_text(), re.S):
    for line in block.splitlines():
        if line.startswith(('Thread ', '#0 ', '#1 ', '#2 ', '331 ', '332 ')):
            assert line in raw, line
            count+=1
print(f'PASS {count} quoted native-stack/syscall lines match raw evidence')
print(f'PASS {round((times[1]-times[0]).total_seconds(),3)}s between stack captures; log size unchanged at {sizes[0]} bytes')
print('PASS app UID 1000; 60-second observation; observer cleanup; no binary modification')
