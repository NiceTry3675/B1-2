#!/usr/bin/env python3
"""Capture live syscall traces and native backtraces, without altering the app."""
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
case = sys.argv[1] if len(sys.argv) > 1 else 'deadlock/diagnostic-01'
dest = (ROOT / 'evidence' / case).resolve()
if os.geteuid() != 0 or not dest.is_relative_to(ROOT / 'evidence'):
    raise SystemExit('Run the diagnostic controller as root inside the isolated container.')
if dest.exists():
    raise SystemExit('Use a new case name; existing evidence is never overwritten.')
command = ['runuser', '-u', 'analyst', '--', 'python3', str(ROOT / 'scripts/run_case.py'),
           case, '--memory', '512', '--cpu', '40', '--multi', 'true', '--seconds', '60']
runner = sp.Popen(command, cwd=ROOT, stdout=sp.DEVNULL)
tracer = None
events = []


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')


def event(message):
    events.append({'at': stamp(), 'message': message})


def snapshot(name, pid):
    with (dest / name).open('w') as f:
        f.write(f'UTC {stamp()}\n'); f.flush()
        sp.run(['ps', '-L', '-p', str(pid), '-o', 'user,pid,ppid,lwp,stat,pcpu,rss,wchan:32,comm'], stdout=f, check=True)
        for task in sorted(Path(f'/proc/{pid}/task').iterdir()):
            for field in ['comm', 'syscall', 'wchan']:
                path = task / field
                f.write(f'\n$ cat {path}\n{path.read_text()}\n')
        f.write(f'app.log bytes={(dest / "app.log").stat().st_size}\n')


try:
    deadline = time.monotonic() + 6
    pid = None
    while time.monotonic() < deadline:
        settings = dest / 'settings.json'
        if settings.exists():
            parent = json.loads(settings.read_text())['launcher_pid']
            for entry in Path('/proc').glob('[0-9]*/exe'):
                try:
                    candidate = int(entry.parent.name)
                    if (candidate != parent and os.getpgid(candidate) == parent
                            and entry.resolve().name == 'agent-leak-app-x86'):
                        pid = candidate
                        break
                except (OSError, ProcessLookupError):
                    continue
        if pid:
            break
        if runner.poll() is not None:
            raise RuntimeError('App runner exited before attach')
        time.sleep(.05)
    if not pid:
        raise RuntimeError('Workload child was not found before worker startup')
    event(f'workload PID={pid}; app launched through runuser -u analyst')
    with (dest / 'diagnostic-tools.txt').open('w') as f:
        for cmd in [['id'], ['strace', '--version'], ['gdb', '--version'], ['sha256sum', str(ROOT/'agent-app-leak/agent-leak-app-x86')]]:
            f.write('$ ' + ' '.join(cmd) + '\n'); f.flush()
            sp.run(cmd, stdout=f, stderr=sp.STDOUT, check=True)
    trace_cmd = ['strace', '-f', '-tt', '-T', '-s', '512', '-e', 'trace=futex,write',
                 '-p', str(pid), '-o', str(dest / 'strace-futex-write.txt')]
    event(' '.join(trace_cmd))
    with (dest / 'strace-attach.txt').open('w') as f:
        tracer = sp.Popen(trace_cmd, stdout=f, stderr=sp.STDOUT)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        app_log = (dest / 'app.log').read_text()
        if app_log.count('(Status: BLOCKED)') >= 2:
            break
        if tracer.poll() is not None:
            raise RuntimeError('strace exited before deadlock')
        time.sleep(.1)
    else:
        raise RuntimeError('Both BLOCKED logs were not observed')
    time.sleep(3)
    snapshot('proc-during-strace.txt', pid)
    tracer.send_signal(signal.SIGINT)
    tracer.wait(timeout=5)
    event(f'strace detached, returncode={tracer.returncode}')
    # ptrace permits one tracer per task; detach strace before attaching GDB.
    for index in [1, 2]:
        if index == 2:
            time.sleep(15)
        gdb_cmd = ['gdb', '-q', '-nx', '-batch', '-iex', 'set auto-load off',
                   '-ex', 'set debuginfod enabled off', '-ex', 'set pagination off',
                   '-ex', 'set print frame-arguments none', '-ex', 'info threads',
                   '-ex', 'thread apply all bt 12', '-ex', 'detach', '-p', str(pid)]
        event(f'gdb capture {index} start: ' + ' '.join(gdb_cmd))
        with (dest / f'gdb-thread-bt-{index:02}.txt').open('w') as f:
            f.write(f'UTC {stamp()}\n$ ' + ' '.join(gdb_cmd) + '\n'); f.flush()
            sp.run(gdb_cmd, stdout=f, stderr=sp.STDOUT, timeout=15, check=True)
        snapshot(f'proc-after-gdb-{index:02}.txt', pid)
        event(f'gdb capture {index} detached')
    runner.wait(timeout=65)
    if runner.returncode:
        raise RuntimeError(f'App runner failed: {runner.returncode}')
    event('60-second observation finished; app cleanup is recorded in result.json')
finally:
    if tracer is not None and tracer.poll() is None:
        tracer.send_signal(signal.SIGINT)
        tracer.wait(timeout=5)
    # Allow the existing runner to preserve evidence and clean up its app.
    if runner.poll() is None:
        runner.wait(timeout=70)
    if dest.exists():
        (dest / 'diagnostic-events.json').write_text(json.dumps(events, indent=2) + '\n')
print(json.dumps(events, indent=2))
