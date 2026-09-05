#!/usr/bin/env python3
"""Run the supplied binary unchanged; capture Linux process and log evidence."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import time
import pty
import threading

ROOT = Path(__file__).resolve().parents[1]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("case")
    p.add_argument("--memory", type=int, required=True)
    p.add_argument("--cpu", type=int, required=True)
    p.add_argument("--multi", choices=["true", "false"], required=True)
    p.add_argument("--seconds", type=int, default=180)
    args = p.parse_args()
    if os.getuid() == 0 or not Path('/proc').exists():
        p.error('Linux 일반 사용자로 실행하세요.')
    if not 50 <= args.memory <= 512 or not 10 <= args.cpu <= 100 or args.seconds <= 0:
        p.error('환경변수 범위 또는 관측 시간이 유효하지 않습니다.')
    dest = (ROOT / 'evidence' / args.case).resolve()
    if not dest.is_relative_to(ROOT / 'evidence'):
        p.error('증거 경로는 evidence 내부여야 합니다.')
    dest.mkdir(parents=True, exist_ok=False)
    runtime = ROOT / 'runtime' / args.case
    for directory in ['upload_files', 'api_keys', 'logs']:
        (runtime / directory).mkdir(parents=True, exist_ok=True)
    key = runtime / 'api_keys/secret.key'
    key.write_text('agent_api_key_test\n')
    key.chmod(0o600)
    env = os.environ.copy()
    settings = dict(AGENT_HOME=str(runtime), AGENT_PORT='15034',
                    AGENT_UPLOAD_DIR=str(runtime / 'upload_files'),
                    AGENT_KEY_PATH=str(runtime / 'api_keys'),
                    AGENT_LOG_DIR=str(runtime / 'logs'), MEMORY_LIMIT=str(args.memory),
                    CPU_MAX_OCCUPY=str(args.cpu), MULTI_THREAD_ENABLE=args.multi)
    env.update(settings)
    arch = os.uname().machine
    binary = ROOT / 'agent-app-leak' / ('agent-leak-app-x86' if arch == 'x86_64' else 'agent-leak-app-arm64')
    with (dest / 'preflight.txt').open('w') as f:
        for cmd in [['id'], ['uname', '-a'], ['cat', '/etc/os-release'], ['free', '-m'],
                    ['ss', '-ltnp'], ['sha256sum', str(binary)]]:
            f.write('$ ' + ' '.join(cmd) + '\n'); f.flush()
            sp.run(cmd, stdout=f, stderr=sp.STDOUT, check=True)
        for name in ['cpu.max', 'memory.max', 'memory.events']:
            file = Path('/sys/fs/cgroup') / name
            if file.exists():
                f.write(f'\n$ cat {file}\n{file.read_text()}')
    import socket
    with socket.socket() as sock:
        sock.bind(('0.0.0.0', 15034))
    start = time.monotonic()
    meta = dict(started_at=now(), settings=settings, binary=str(binary),
                command=[str(binary)], observation_limit_seconds=args.seconds)
    monitors = {}
    samplers = {}
    stopped = False
    with (dest / 'app.log').open('w') as log, (dest / 'ps.txt').open('w') as pslog, (dest / 'top.txt').open('w') as toplog:
        # A terminal makes Python's stdout line-buffered, preserving the last
        # print() before the app kills itself with SIGKILL or SIGTERM.
        master, slave = pty.openpty()
        app = sp.Popen([str(binary)], cwd=ROOT, env=env, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        def drain():
            while True:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                log.write(data.decode('utf-8', errors='replace').replace('\r\n', '\n'))
                log.flush()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        meta['launcher_pid'] = app.pid
        (dest / 'settings.json').write_text(json.dumps(meta, indent=2) + '\n')
        last_snapshot = -10
        try:
            while app.poll() is None:
                elapsed = time.monotonic() - start
                # PyInstaller onefile can launch a child with the same executable.
                pids = []
                for entry in Path('/proc').glob('[0-9]*/exe'):
                    try:
                        pid = int(entry.parent.name)
                        if entry.resolve() == binary and os.getpgid(pid) == app.pid:
                            pids.append(pid)
                    except (OSError, ProcessLookupError):
                        pass
                for pid in sorted(pids):
                    if pid not in monitors:
                        mon_env = env | {'MONITOR_LOG_FILE': str(dest / f'monitor-{pid}.log')}
                        monitors[pid] = sp.Popen(['bash', '-c', 'while bash "$1" "$2"; do :; done', '_',
                                                 str(ROOT / 'monitor.sh'), str(pid)], env=mon_env,
                                                stdout=sp.DEVNULL, stderr=sp.DEVNULL, start_new_session=True)
                        if args.case.startswith('cpu/'):
                            with (dest / f'cpu-burst-{pid}.csv').open('w') as burst:
                                samplers[pid] = sp.Popen(['python3', str(ROOT / 'scripts/sample_cpu.py'), str(pid), str(args.seconds)],
                                                        stdout=burst, stderr=sp.DEVNULL)
                if elapsed - last_snapshot >= 5:
                    pslog.write(f'\n[{now()}] elapsed={elapsed:.3f}s\n$ ps -ef | grep [a]gent-leak-app\n'); pslog.flush()
                    sp.run(['bash', '-c', 'ps -ef | grep "[a]gent-leak-app"'], stdout=pslog, stderr=sp.STDOUT)
                    if pids:
                        ids = ','.join(map(str, sorted(pids)))
                        cmd = ['ps', '-L', '-p', ids, '-o', 'pid,ppid,lwp,nlwp,stat,pcpu,pmem,rss,time,wchan:32,comm']
                        pslog.write('$ ' + ' '.join(cmd) + '\n'); pslog.flush()
                        sp.run(cmd, stdout=pslog, stderr=sp.STDOUT)
                        sp.run(['ps', '-L', '-p', ids, '-o', 'pid,lwp,cls,rtprio,ni,pri'], stdout=pslog, stderr=sp.STDOUT)
                        toplog.write(f'\n[{now()}] elapsed={elapsed:.3f}s\n'); toplog.flush()
                        sp.run(['top', '-b', '-H', '-n', '1', '-w', '160', '-p', ids], stdout=toplog, stderr=sp.STDOUT)
                    pslog.write(f'app.log bytes={os.fstat(log.fileno()).st_size}\n'); pslog.flush()
                    last_snapshot = elapsed
                if elapsed >= args.seconds:
                    stopped = True
                    meta['observer_stop_at'] = now()
                    os.killpg(app.pid, signal.SIGTERM)
                    break
                time.sleep(0.2)
            try:
                rc = app.wait(timeout=5)
            except sp.TimeoutExpired:
                os.killpg(app.pid, signal.SIGKILL)
                rc = app.wait(timeout=5)
        finally:
            if app.poll() is None:
                os.killpg(app.pid, signal.SIGKILL)
                app.wait()
            for mon in monitors.values():
                if mon.poll() is None:
                    os.killpg(mon.pid, signal.SIGTERM)
                mon.wait()
            for sampler in samplers.values():
                if sampler.poll() is None:
                    sampler.terminate()
                sampler.wait()
            reader.join(timeout=3)
            os.close(master)
    import shutil
    shutil.copytree(runtime / 'logs', dest / 'application-logs')
    meta.update(ended_at=now(), elapsed_seconds=round(time.monotonic() - start, 3),
                returncode=rc, observer_stopped=stopped, observed_pids=sorted(monitors))
    (dest / 'result.json').write_text(json.dumps(meta, indent=2) + '\n')
    with (dest / 'postflight.txt').open('w') as f:
        sp.run(['ss', '-ltnp'], stdout=f, stderr=sp.STDOUT)
        sp.run(['ps', '-ef'], stdout=f, stderr=sp.STDOUT)
        file = Path('/sys/fs/cgroup/memory.events')
        if file.exists():
            f.write(f'\n$ cat {file}\n{file.read_text()}')
    print(json.dumps(meta, indent=2), flush=True)


if __name__ == '__main__':
    main()
