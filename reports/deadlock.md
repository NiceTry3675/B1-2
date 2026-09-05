# [Bug] Deadlock - 두 워커의 반대 락 획득 순서로 작업 진행 정지

## 1. Description (현상 설명)

2026-09-05 15:50:58.028 KST, `MEMORY_LIMIT=512`, `CPU_MAX_OCCUPY=40`, `MULTI_THREAD_ENABLE=true`로 실행했다. 약 9초 뒤 두 워커가 `WAITING ... BLOCKED`를 기록한 후 **로그 증가와 작업 진행이 멈췄다**. 실제 작업 PID 5782는 약 60초 관측이 끝날 때까지 존재했다. 예외 종료가 아니라 살아 있는 프로세스의 교착상태이다.

Ubuntu 22.04.5 / Linux 6.17.8 x86_64 컨테이너에서 UID 1000으로 실행했다. 메모리 상한 1GiB·CPU 상한 2코어·외부 네트워크 없음이며, 필수 경로·키·권한·15034 포트 부트 검사는 모두 통과했다.

```bash
docker run --rm --init --memory=1g --cpus=2 --network=none \
  -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py deadlock/before \
  --memory 512 --cpu 40 --multi true --seconds 60
docker run --rm --init --memory=1g --cpus=2 --network=none \
  -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py deadlock/after \
  --memory 512 --cpu 40 --multi false --seconds 60
```

## 2. Evidence & Logs (증거 자료)

15:51:53 KST 스냅샷의 PID 존재 증거:

```text
$ ps -ef | grep '[a]gent-leak-app'
analyst     5763    5756  0 15:50 ?        00:00:00 /work/agent-app-leak/agent-leak-app-x86
analyst     5782    5763  0 15:50 ?        00:00:00 /work/agent-app-leak/agent-leak-app-x86
```

같은 시점 `ps -L -p 5763,5782 -o pid,ppid,lwp,nlwp,stat,pcpu,pmem,rss,time,wchan:32,comm`에서 작업 PID의 세 LWP는 5782, 6079, 6080이었다. 모두 `SNl`, CPU 0.0%, TIME 00:00:00, `futex_wait`였다. 부모 5763은 `do_wait`로 자식을 기다렸다. 스레드별 RSS는 같은 주소 공간의 값이므로 세 번 더하지 않는다.

`monitor.sh` 원문 발췌:

```text
[2026-09-05T15:51:08+09:00] PID:5782 CPU:0.0% RSS_KB:18556 MEM:0.1% STATE:SNl
[2026-09-05T15:51:25+09:00] PID:5782 CPU:0.0% RSS_KB:18556 MEM:0.1% STATE:SNl
[2026-09-05T15:51:45+09:00] PID:5782 CPU:0.0% RSS_KB:18556 MEM:0.1% STATE:SNl
[2026-09-05T15:51:57+09:00] PID:5782 CPU:0.0% RSS_KB:12300 MEM:0.0% STATE:SNl
```

전체 58개 관제 샘플에서 CPU는 0.0%였다. 15:51:08~45의 RSS는 18,556KiB로 일정했다. 이후 RSS가 12,300KiB로 감소했으므로 전체 구간의 메모리가 완전히 불변했다고 쓰지 않는다. 감소 원인은 이 증거만으로 확정할 수 없고, 감소 후에도 로그·스레드 대기는 해소되지 않았다.

마지막 두 로그는 15:51:07.202에 기록되었다. 아래는 같은 실행의 핵심 메시지 부분만 발췌한 것이다.

```text
[Worker-Thread-1] LOCK ACQUIRED: [Shared_Memory_A]. (Holding...)
[Worker-Thread-2] LOCK ACQUIRED: [Socket_Pool_B]. (Holding...)
[Worker-Thread-1] WAITING for [Socket_Pool_B]... (Status: BLOCKED)
[Worker-Thread-2] WAITING for [Shared_Memory_A]... (Status: BLOCKED)
```

그 뒤 약 51초 동안 새 로그가 없었다. 반복 `ps.txt` 스냅샷에서 `app.log bytes=2657`이 유지되었다. 재시작 전에 PID·관제·스레드·로그 증거를 확보했다.

원본: [실행 로그](../evidence/deadlock/before/app.log), [관제](../evidence/deadlock/before/monitor-5782.log), [PID·스레드·로그 크기](../evidence/deadlock/before/ps.txt), [top -H](../evidence/deadlock/before/top.txt), [Before 결과](../evidence/deadlock/before/result.json).

### 추가 직접 증거: GDB 스택 + strace의 워커/LWP 연결 (FAIL #15)

2026-09-05 **16:28:13~16:29:13 KST**에 동일 기능 설정(512/40/true)으로 별도 진단 실행을 수행했다. 원본 Before/After 수치는 변경하지 않았다. 추가 실행의 실제 작업 PID는 **35**, 부모는 16이며 앱은 기존과 같이 `analyst`(UID 1000)로 실행했다. 진단 전용 컨테이너에 GDB 12.1, strace 5.16을 추가하고 추적 도구만 root로 실행했다.

| 자료 | KST 시간 / 대상 | 원본 |
| --- | --- | --- |
| 시스템 호출 추적 | 16:28:14.039~26.101 / PID 35와 자식 LWP | [strace-futex-write.txt](../evidence/deadlock/diagnostic-01/strace-futex-write.txt) |
| 전체 스레드 스택 1 | 16:28:26.101 / LWP 35, 331, 332 | [gdb-thread-bt-01.txt](../evidence/deadlock/diagnostic-01/gdb-thread-bt-01.txt) |
| 전체 스레드 스택 2 | 16:28:41.242 / 같은 LWP | [gdb-thread-bt-02.txt](../evidence/deadlock/diagnostic-01/gdb-thread-bt-02.txt) |
| 디버거 분리 후 대기 | 16:28:26.220 및 41.360 / 같은 LWP | [proc 1](../evidence/deadlock/diagnostic-01/proc-after-gdb-01.txt), [proc 2](../evidence/deadlock/diagnostic-01/proc-after-gdb-02.txt) |
| 앱·환경·종료 기록 | 60초 관측, 관측자 SIGTERM | [앱 로그](../evidence/deadlock/diagnostic-01/app.log), [환경](../evidence/deadlock/diagnostic-01/preflight.txt), [결과](../evidence/deadlock/diagnostic-01/result.json), [수집 시각·명령](../evidence/deadlock/diagnostic-01/diagnostic-events.json) |

**이름과 LWP의 직접 연결:** strace는 각 `write()`를 호출한 Linux TID를 행 맨 앞에 표시한다. 16:28:23.003206의 TID 331이 Worker-Thread-1의 `WAITING for [Socket_Pool_B]`를 stdout에 썼고, 16:28:23.003704의 TID 332가 Worker-Thread-2의 `WAITING for [Shared_Memory_A]`를 썼다. 같은 TID의 앞선 LOCK ACQUIRED 출력까지 연결하면 다음 관계가 성립한다. OS comm은 둘 다 잘린 실행 파일명이라 comm만으로 이름을 추정하지 않았다.

| 앱 이름 / LWP | 로그의 보유 자원 | 다음 요청 자원 | 마지막 지속 futex 대기 주소 |
| --- | --- | --- | --- |
| Worker-Thread-1 / 331 | Shared_Memory_A | Socket_Pool_B | 0x1214e2b0 |
| Worker-Thread-2 / 332 | Socket_Pool_B | Shared_Memory_A | 0x12154e90 |

strace의 마지막 대기 호출 원문:

```text
331   16:28:23.003371 futex(0x1214e2b0, FUTEX_WAIT_BITSET_PRIVATE|FUTEX_CLOCK_REALTIME, 0, NULL, FUTEX_BITSET_MATCH_ANY <unfinished ...>
332   16:28:23.003865 futex(0x12154e90, FUTEX_WAIT_BITSET_PRIVATE|FUTEX_CLOCK_REALTIME, 0, NULL, FUTEX_BITSET_MATCH_ANY <detached ...>
```

`NULL`은 이 futex 대기 호출에 제한 시간이 전달되지 않았음을 나타낸다. `<detached ...>`는 수집기가 strace에 SIGINT를 보내 분리한 표기이며, 앱의 락 획득 성공 또는 종료가 아니다. 추적 중 나타나는 다른 futex 주소에는 로깅·런타임 동기화도 포함될 수 있어 모든 futex를 업무 자원 락으로 분류하지 않았다.

**GDB 콜스택 원문 발췌:** 1차 캡처에서 각 워커의 #0~#2 프레임은 아래와 같다. 두 번째 캡처에도 같은 PC와 `PyThread_acquire_lock_timed` 프레임이 유지되었다. 전체 12개 프레임/스레드는 위 원본에 보존했다.

```text
Thread 3 (Thread 0x7fe791277640 (LWP 332) "agent-leak-app-"):
#0  0x00007fe792c9b0d7 in ?? () from /lib/x86_64-linux-gnu/libc.so.6
#1  0x00007fe792ca6c38 in ?? () from /lib/x86_64-linux-gnu/libc.so.6
#2  0x00007fe79284ecec in PyThread_acquire_lock_timed () from /tmp/_MEILP5dBf/libpython3.10.so.1.0
Thread 2 (Thread 0x7fe791a78640 (LWP 331) "agent-leak-app-"):
#0  0x00007fe792c9b0d7 in ?? () from /lib/x86_64-linux-gnu/libc.so.6
#1  0x00007fe792ca6c38 in ?? () from /lib/x86_64-linux-gnu/libc.so.6
#2  0x00007fe79284ecec in PyThread_acquire_lock_timed () from /tmp/_MEILP5dBf/libpython3.10.so.1.0
```

두 GDB 캡처 사이 시작 시각 차이는 **15.141초**다. GDB를 분리한 직후 `/proc/35/task/331/syscall`은 두 번 모두 `202 0x1214e2b0 0x189 ...`, LWP 332는 `202 0x12154e90 0x189 ...`를 나타냈다. 이 x86_64 환경의 syscall 202를 strace가 futex로 해석한 결과와 일치한다. 두 LWP의 wchan은 `__futex_wait`, 앱 로그 크기는 모두 **2,664바이트**였다. 즉 디버거가 멈춘 순간만의 상태가 아니라, 디버거 분리 후에도 같은 자원 대기가 지속되었다.

**수집 명령과 재현:** 기존 이미지를 빌드하고 진단 이미지를 추가한다. 마지막 인자는 새 증거 폴더명으로 바꿔 재실험할 수 있다.

```bash
docker build -t b1-2-lab .
docker build -f Dockerfile.diagnostics -t b1-2-diagnostics .
docker run --rm --init --memory=1g --cpus=2 --network=none \
  --cap-add=SYS_PTRACE --security-opt=seccomp=unconfined --user root \
  -v "$PWD:/work" b1-2-diagnostics \
  python3 scripts/capture_deadlock.py deadlock/diagnostic-rerun-01
```

수집기는 `runuser -u analyst`로 기존 실험 실행기를 시작한 뒤, 작업 자식에 `strace -f -tt -T -s 512 -e trace=futex,write -p PID`를 붙인다. BLOCKED 두 건 확인 후 strace를 먼저 분리하고, `gdb -q -nx -batch -ex 'info threads' -ex 'thread apply all bt 12' -ex detach -p PID`로 두 번 스택을 수집한다. 실제 옵션 전체와 UTC 시각은 diagnostic-events.json에 남겼다. 앱 실행 파일의 해시는 최초 실험과 같다.

**영향과 확인 범위:** GDB는 attach 시 잠깐 앱을 정지시키며 strace도 실행 타이밍에 영향을 준다. 따라서 추가 실행을 기존 생존 시간 통계에 섞지 않았다. 메모리 쓰기, 함수 호출 주입, 락 강제 해제, 디스어셈블·디컴파일은 수행하지 않았다. 바이너리에 디버그 정보가 없어 일부 네이티브 프레임은 `??`이며 Python 소스 줄 번호는 복원하지 않았다. 그럼에도 **로그를 쓴 실제 워커 TID → 무기한 futex 대기 → Python 락 획득 콜스택 → 시간 경과 후 같은 대기 유지**를 직접 증거로 연결했다. A/B의 의미와 소유 관계는 앱 로그에 근거하며 락 객체 내부의 소유자 필드를 읽어낸 것은 아니다.

명령 의미 참고: [GDB 전체 스레드 backtrace 공식 문서](https://www.sourceware.org/gdb/current/onlinedocs/gdb.html/Backtrace.html), [strace 공식 사용 안내](https://strace.io/).

## 3. Root Cause Analysis (원인 분석)

로그가 나타내는 대기 관계는 다음과 같다. 화살표는 해당 자원 또는 작업의 완료를 기다린다는 뜻이다.

```text
Worker-1 holds A; waits for B held by Worker-2
Worker-2 holds B; waits for A held by Worker-1
Worker-1 -> B -> Worker-2 -> A -> Worker-1
```

교착상태의 네 조건이 모두 성립하는 패턴이다.

| 조건 | 이 실행에서의 근거 |
| --- | --- |
| 상호 배제 | A/B 각각 LOCK ACQUIRED와 상대 워커 BLOCKED |
| 점유 대기 | A 또는 B를 Holding한 채 두 번째 자원을 요청 |
| 비선점 | 상대 소유 자원이 반환되지 않고 대기 지속; 강제 회수 로그 없음 |
| 순환 대기 | Worker-1 → B → Worker-2 → A → Worker-1 |

정상적인 휴면 상태도 CPU가 0이고 `futex_wait`일 수 있다. 이번에는 서로의 자원을 기다리는 명시적 로그, 세 LWP의 대기, 장시간 작업 로그 정지가 함께 있으므로 순환 대기에 따른 교착상태로 판단한다. 메인 스레드는 `Waiting for worker threads to complete transactions...` 후 기다리므로 워커 완료 대기와 부합한다. 최초 Before 실행은 이름/LWP 매핑이 없었지만, 추가 진단 실행에서는 strace의 write 호출로 Worker-1/2와 LWP 331/332를 직접 연결했다.

OS는 락을 기다리는 스레드를 재우므로 CPU를 계속 소비하지 않아도 프로세스가 멈출 수 있다. 다른 스레드가 락을 해제할 때까지 acquire가 대기하는 일반 원리는 [Python threading 공식 문서](https://docs.python.org/3/library/threading.html#lock-objects)를 참고했다. 최초 분석은 로그와 ps에 근거했고, 추가 진단에서 GDB 콜스택·strace·분리 후 syscall 상태를 확보해 실제 락 획득 대기까지 확인했다. 내부 락 소유자 필드나 소스 코드는 조사하지 않았다.

## 4. Workaround & Verification (조치 및 검증)

| 비교 항목 | Before | After |
| --- | --- | --- |
| MULTI_THREAD_ENABLE | true | false |
| 고정 기능 설정 | MEMORY=512, CPU=40 | MEMORY=512, CPU=40 |
| 시작 KST / 작업 PID | 15:50:58.028 / 5782 | 15:51:58.179 / 8441 |
| 관측 시간 | 60.063초 | 60.356초 |
| 마지막 앱 로그 | 15:51:07.202 BLOCKED | 15:52:57.267 Current Load |
| CPU 1초 샘플 | 모두 0.0% | 0.0~7.9% |
| RSS | 18,556KiB 정체 구간 | 18,372 → 503,376KiB |
| 진행 확인 | 로그 2,657바이트로 정지 | A/B/C 완료 후 Heap/Load 지속 |
| 관측 종료 | 관측자 SIGTERM | 관측자 SIGTERM |
| 결과 원본 | [Before](../evidence/deadlock/before/result.json) | [After](../evidence/deadlock/after/result.json) |

After에는 `Healthy System Monitoring`, `All tasks completed.` 이후 MemoryWorker/CpuWorker 로그가 계속 나왔고, 관측 중 `WAITING ... BLOCKED`는 없었다. [After 로그](../evidence/deadlock/after/app.log), [After ps](../evidence/deadlock/after/ps.txt), [After 관제](../evidence/deadlock/after/monitor-8441.log)에서 확인한다.

MULTI_THREAD_ENABLE=false는 문제의 동시 트랜잭션 경로를 회피하는 조치다. **false에서도 정상 MemoryWorker/CpuWorker를 포함해 3 LWP가 관측**되므로 앱 전체가 단일 OS 스레드가 되었다고 해석하지 않는다. 두 실험 모두 관측자가 종료했고 그 이전에 자체 종료는 없었다. 60초 관측은 영구 무교착 보장이 아니다.

근본 개선은 두 경로의 락 획득 순서를 A→B로 통일하여 순환 대기 조건을 없애는 것이다. 추가로 락 획득 timeout과 실패 시 이미 잡은 락 반환을 설계하고, 반복 경쟁 상황에서도 트랜잭션이 완료되는지 검증할 수 있다.
