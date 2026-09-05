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

정상적인 휴면 상태도 CPU가 0이고 `futex_wait`일 수 있다. 이번에는 서로의 자원을 기다리는 명시적 로그, 세 LWP의 대기, 장시간 작업 로그 정지가 함께 있으므로 순환 대기에 따른 교착상태로 판단한다. 메인 스레드는 `Waiting for worker threads to complete transactions...` 후 기다리므로 워커 완료 대기와 부합한다. 다만 OS LWP 번호와 앱의 Worker-1/2 이름을 직접 매핑한 것은 아니다.

OS는 락을 기다리는 스레드를 재우므로 CPU를 계속 소비하지 않아도 프로세스가 멈출 수 있다. 다른 스레드가 락을 해제할 때까지 acquire가 대기하는 일반 원리는 [Python threading 공식 문서](https://docs.python.org/3/library/threading.html#lock-objects)를 참고했다. 바이너리 내부 락 객체나 콜스택을 조사하지 않고 로그와 시스템 도구로 추론했다.

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
