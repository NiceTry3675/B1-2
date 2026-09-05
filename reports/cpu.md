# [Bug] CPU Latency - CPU 작업 중 Watchdog가 SIGTERM 보호 종료

## 1. Description (현상 설명)

2026-09-05 15:49:12.258 KST에 `MEMORY_LIMIT=512`, `CPU_MAX_OCCUPY=100`, `MULTI_THREAD_ENABLE=false`로 실행한 앱에서 내부 `Current Load`가 5.00%에서 52.69%로 증가했다. **30.504초 후 Watchdog가 SIGTERM 종료**를 실행했다. 실제 작업 PID는 1089이며 부모 실행기는 1069이다.

환경: Ubuntu 22.04.5 / Linux 6.17.8 x86_64, UID 1000, 컨테이너 메모리 1GiB·CPU 상한 2코어·외부 네트워크 없음. 필수 경로·키·포트·쓰기 권한의 부트 검사가 모두 통과했다.

```bash
docker run --rm --init --memory=1g --cpus=2 --network=none \
  -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py cpu/before-02 \
  --memory 512 --cpu 100 --multi false --seconds 100
docker run --rm --init --memory=1g --cpus=2 --network=none \
  -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py cpu/after \
  --memory 512 --cpu 40 --multi false --seconds 75
```

**측정 범위:** 특정 프로세스의 짧은 CPU 상승과 보호 종료를 확인했다. 별도 클라이언트의 응답 시간을 측정하지 않았으므로 실제 서비스 지연량이나 시스템 전체 CPU 포화를 주장하지 않는다.

## 2. Evidence & Logs (증거 자료)

Before 앱 원문:

```text
2026-09-05 15:49:14,389 [INFO] [CpuWorker] Current Load: 5.00%
2026-09-05 15:49:39,324 [INFO] [CpuWorker] Current Load: 48.91%
2026-09-05 15:49:42,442 [INFO] [CpuWorker] Current Load: 52.69%
2026-09-05 15:49:42,544 [CRITICAL] [CpuWorker] CPU Threshold Violated! (52.690000000000005%).
>>> [SYSTEM] WATCHDOG: INITIATING EMERGENCY ABORT (SIGTERM) <<<
```

1초 간격 `monitor.sh`에서는 PID 1089가 **0.0~5.0%**로 관측되었다. 짧은 연산을 1초 평균만으로 판단하지 않기 위해 `/proc/PID/stat`의 utime+stime 차이를 약 50ms 간격으로 추가 수집했다. CPU는 코어 하나를 100%로 계산한다. 다음 CSV 원문은 UTC이며 KST는 +9시간이다.

```text
2026-09-05T06:49:42.373+00:00,1089,0.0,0.051495,S,16488
2026-09-05T06:49:42.424+00:00,1089,0.0,0.050968,S,16488
2026-09-05T06:49:42.475+00:00,1089,78.6,0.050898,R,16488
2026-09-05T06:49:42.526+00:00,1089,39.1,0.051105,S,16488
```

열은 `timestamp,pid,cpu_percent,interval_seconds,state,rss_kb`이다. 0%에서 **78.6%**로 급상승한 구간에 R(실행/실행 가능) 상태를 관측했고, 직후 앱이 임계치 위반을 기록했다. 약 10ms 단위 CPU tick을 50ms 구간에서 계산하므로 단일 샘플의 정밀도에는 한계가 있다.

원본: [앱 Before](../evidence/cpu/before-02/app.log), [앱 After](../evidence/cpu/after/app.log), [관제 Before](../evidence/cpu/before-02/monitor-1089.log), [관제 After](../evidence/cpu/after/monitor-2443.log), [50ms Before](../evidence/cpu/before-02/cpu-burst-1089.csv), [50ms After](../evidence/cpu/after/cpu-burst-2443.csv), [ps](../evidence/cpu/before-02/ps.txt), [top](../evidence/cpu/before-02/top.txt).

## 3. Root Cause Analysis (원인 분석)

**직접 원인:** CpuWorker가 부하 증가를 기록하다 내부 정책상 허용 범위를 위반했고, Watchdog가 비상 중단을 선언했다. Python 종료 상태 -15, `observer_stopped=false`, 종료 후 PID·소켓 소멸이 보호 종료를 뒷받침한다. 일반 예외의 스택 트레이스나 관측자의 강제 중단이 원인이라는 증거는 없다.

**설정 해석:** CPU_MAX_OCCUPY=100을 “실측 CPU가 100%가 되어야 종료된다”로 해석하면 안 된다. 실제 종료 로그의 내부 값은 52.69%였고, 부트 화면에는 `Recommend Under 50%`가 표시되었다. CPU_MAX_OCCUPY는 앱의 작업 부하 동작에 영향을 주지만, 관측만으로 Watchdog의 내부 비교식 전체를 확정할 수 없다. 40으로 변경한 실행에서는 40.00% 도달 시 cooldown이 시작되어 종료를 회피했다.

**OS 원리:** 실행 가능한 작업들은 CPU 실행 시간을 경쟁한다. CPU를 오래 사용하는 작업이 증가하면 다른 작업의 실행 대기가 길어질 수 있다. 스케줄러의 nice 우선순위 조정은 실행 시간 배분에 영향을 주지만 고정된 CPU 사용률 상한을 보장하지 않는다. 앱은 nice=10을 기록했다. [Linux 스케줄러 공식 문서](https://www.kernel.org/doc/html/latest/scheduler/sched-design-CFS.html)를 참고했다.

**측정 해석과 한계:** 앱의 `Current Load`는 OS 실측 CPU와 다른 지표이다. 52.69%를 시스템 CPU 사용률로 옮겨 적지 않았다. 1초 관제, 50ms 관제, ps의 실행 기간 평균도 서로 다른 시간 창이다. After는 MemoryWorker까지 작동하므로 실제 CPU 최대치가 오히려 커졌다. 따라서 “실측 CPU가 낮아졌다”가 아니라 “작업 정책이 cooldown으로 전환되고 관측 중 자체 종료가 사라졌다”가 검증 결과다.

## 4. Workaround & Verification (조치 및 검증)

| 비교 항목 | Before | After |
| --- | --- | --- |
| CPU_MAX_OCCUPY | 100% | 40% |
| 고정 기능 설정 | MEMORY=512, MULTI=false | MEMORY=512, MULTI=false |
| 시작 KST / 작업 PID | 15:49:12.258 / 1089 | 15:49:42.848 / 2443 |
| 생존 / 관측 시간 | 30.504초 후 자체 종료 | 75.093초까지 생존 |
| 앱 내부 Load | 5.00 → 52.69% | 40.00%에서 cooldown |
| 1초 실측 CPU 최대 | 5.0% | 7.9% |
| 약 50ms 실측 CPU 최대 | 78.6% | 99.2% |
| 종료 주체 | Watchdog / SIGTERM | 관측자 / SIGTERM |
| observer_stopped | false | true |
| 결과 원본 | [Before](../evidence/cpu/before-02/result.json) | [After](../evidence/cpu/after/result.json) |

After 15:50:12.842에 `Peak reached (40.00%). Starting cooldown...`, 15:50:37.801에 `Cooldown complete (5.00%). Resuming load increase...`를 확인했다. Before 종료 시점을 넘겨 **약 2.46배 길게 관측**했으며 Watchdog 종료는 없었다. 동일한 -15라도 After는 실험 종료를 위해 관측자가 보낸 신호이므로 장애로 계산하지 않는다.

기능 변수 하나만 변경했고 실험별 디렉터리는 격리했다. 부하 값의 증가 폭에는 실행 간 차이가 있으므로 단일 쌍의 생존 시간을 보장값으로 사용하지 않는다. 정상화 설정은 MEMORY_LIMIT=512, CPU_MAX_OCCUPY=40, MULTI_THREAD_ENABLE=false이다. 실제 운영 성능 검증에는 요청 지연·처리량 및 더 긴 관측이 추가로 필요하다.
