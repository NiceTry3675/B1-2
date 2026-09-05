#!/usr/bin/env python3
"""Check report citations and invariants against the collected run evidence."""
import json
from pathlib import Path
import re
from summarize import CASES, read_case

ROOT = Path(__file__).resolve().parents[1]
checks=[]
infos={}
for case in CASES:
    info,rows=read_case(case)
    infos[case]=info
    folder=ROOT/'evidence'/case
    log=(folder/'app.log').read_text()
    assert 'All Boot Checks Passed!' in log,case
    assert rows and all(r['rss_kb']>0 for r in rows),case
    assert len(info['observed_pids'])==2,case
    assert 'oom_kill 0' in (folder/'postflight.txt').read_text(),case
    assert 'agent-leak-app-' not in (folder/'postflight.txt').read_text(),case
    assert (folder/'application-logs/agent_app.log').stat().st_size>0,case
    checks.append(f'PASS {case}: boot, child PID, monitor, no residual app, oom_kill=0')
for case in ['oom/before','oom/after']:
    log=(ROOT/'evidence'/case/'app.log').read_text()
    assert 'Memory limit exceeded' in log and 'SELF-TERMINATED' in log
    assert infos[case]['returncode']==-9 and not infos[case]['observer_stopped']
assert infos['oom/after']['elapsed_seconds']>infos['oom/before']['elapsed_seconds']
log=(ROOT/'evidence/cpu/before-02/app.log').read_text()
assert 'WATCHDOG: INITIATING EMERGENCY ABORT (SIGTERM)' in log
assert infos['cpu/before-02']['returncode']==-15 and not infos['cpu/before-02']['observer_stopped']
after=(ROOT/'evidence/cpu/after/app.log').read_text()
assert 'WATCHDOG' not in after and 'Starting cooldown' in after and 'MEMORY RECOVERED' in after
assert infos['cpu/after']['observer_stopped'] and infos['cpu/after']['elapsed_seconds']>75
before=(ROOT/'evidence/deadlock/before/app.log').read_text()
assert 'WAITING for [Socket_Pool_B]' in before and 'WAITING for [Shared_Memory_A]' in before
assert 'LOCK ACQUIRED: [Shared_Memory_A]' in before and 'LOCK ACQUIRED: [Socket_Pool_B]' in before
assert infos['deadlock/before']['max_cpu']==0
assert 'futex_wait' in (ROOT/'evidence/deadlock/before/ps.txt').read_text()
after=(ROOT/'evidence/deadlock/after/app.log').read_text()
assert 'BLOCKED' not in after and 'All tasks completed' in after and 'Current Load' in after
for a,b,variable in [('oom/before','oom/after','MEMORY_LIMIT'),('cpu/before-02','cpu/after','CPU_MAX_OCCUPY'),('deadlock/before','deadlock/after','MULTI_THREAD_ENABLE')]:
    for key in ['MEMORY_LIMIT','CPU_MAX_OCCUPY','MULTI_THREAD_ENABLE']:
        assert (infos[a]['settings'][key]!=infos[b]['settings'][key])==(key==variable)
checks.append('PASS causal comparisons: only one functional variable differs per pair')
raw='\n'.join(p.read_text() for p in (ROOT/'evidence').rglob('*') if p.is_file() and p.suffix in ['.log','.txt','.csv'])
count=0
for report in (ROOT/'reports').glob('*.md'):
    source=report.read_text()
    assert '미실험' not in source and '{기록}' not in source
    for label,target in re.findall(r'\[([^\]]+)\]\(([^)]+)\)',source):
        if not target.startswith('https://'):
            assert (report.parent/target).exists(),(report,target)
    for block in re.findall(r'```text\n(.*?)\n```',source,re.S):
        for line in block.splitlines():
            if line.startswith(('[2026','2026-','[Worker-Thread','>>> [SYSTEM]')):
                assert line in raw,(report,line)
                count+=1
checks.append(f'PASS {count} quoted evidence lines matched actual source text')
checks.append('PASS all Markdown report links resolve locally; no unfilled report templates')
checks.append('PASS OOM SIGKILL, CPU Watchdog SIGTERM, observer cleanup, deadlock and recovery assertions')
print('\n'.join(checks))
