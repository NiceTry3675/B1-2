# [Bug] OOM Crash - 메모리 누적 후 MemoryGuard가 SIGKILL로 자체 종료

## 1. Description (현상 설명)

2026-09-05 15:48 KST, 제공된 `agent-leak-app-x86`를 일반 사용자 `analyst`(UID 1000)로 실행했다. `MEMORY_LIMIT=100`, `CPU_MAX_OCCUPY=40`, `MULTI_THREAD_ENABLE=false` 조건에서 RSS가 단계적으로 증가했고, 시작 후 **11.412초에 자체 종료**되었다. 메모리 한도를 200MB로 올려도 **23.687초 후 같은 장애**가 재현되었다.

환경은 OrbStack의 Ubuntu 22.04.5 LTS 컨테이너, Linux 6.17.8, x86_64이다. 컨테이너 메모리 제한은 1GiB, CPU 할당 상한은 2코어이며 외부 네트워크는 차단했다. 컨테이너 내부 0.0.0.0:15034 바인딩은 가능하다. 모든 부트 검사 6개가 통과했다.

프로젝트 루트에서 이미지를 빌드한 뒤 각 명령을 실행한다. 기존 증거 폴더가 있으면 실행기가 덮어쓰기를 거부하므로 재실험에는 새 이름을 사용한다.

```bash
docker build -t b1-2-lab .
docker run --rm --init --memory=1g --cpus=2 --network=none \
  -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py oom/before \
  --memory 100 --cpu 40 --multi false --seconds 90
docker run --rm --init --memory=1g --cpus=2 --network=none \
  -v "$PWD:/work" b1-2-lab python3 scripts/run_case.py oom/after \
  --memory 200 --cpu 40 --multi false --seconds 90
```

## 2. Evidence & Logs (증거 자료)

실제 작업 PID는 Before **33**, After **34**이다. 부모 실행기 PID 14/15는 자식을 기다리므로 부모의 작은 RSS를 앱 메모리로 오인하지 않았다. 부트 로그, PID 계층, 실행 파일 경로로 대상을 확인했다.

`monitor.sh` 원문에서 발췌했다. RSS_KB는 KiB 단위이고 MEM은 Linux가 보고하는 전체 메모리 대비 비율이다. MEM을 앱 한도 소진율로 해석하지 않았다.

```text
[2026-09-05T15:48:16+09:00] PID:33 CPU:0.0% RSS_KB:18512 MEM:0.1% STATE:SN
[2026-09-05T15:48:20+09:00] PID:33 CPU:4.0% RSS_KB:69720 MEM:0.4% STATE:SN
[2026-09-05T15:48:25+09:00] PID:33 CPU:0.0% RSS_KB:95324 MEM:0.5% STATE:SN
[2026-09-05T15:49:11+09:00] PID:34 CPU:0.0% RSS_KB:197708 MEM:1.2% STATE:SN
```

Before 종료 직전 및 터미널 출력:

```text
2026-09-05 15:48:25,988 [INFO] [MemoryWorker] Current Heap: 100MB
2026-09-05 15:48:25,989 [CRITICAL] [MemoryGuard] Memory limit exceeded (100MB >= 100MB) / (Recommend Over 256MB)
2026-09-05 15:48:25,989 [CRITICAL] [MemoryGuard] Self-terminating process 33 to prevent system instability.
>>> [SYSTEM] SELF-TERMINATED (Memory Limit Exceeded) <<<
```

After에서도 15:49:12.041에 `Memory limit exceeded (200MB >= 200MB)`가 기록되었다. 두 실행 모두 Python `Popen.returncode=-9`(SIGKILL), `observer_stopped=false`였다. 종료 후 `ps`에서 앱이 사라지고 리슨 소켓도 사라졌다. `memory.events`의 `oom`, `oom_kill`은 모두 0이다.

원본: [Before 실행 로그](../evidence/oom/before/app.log), [After 실행 로그](../evidence/oom/after/app.log), [Before 관제](../evidence/oom/before/monitor-33.log), [After 관제](../evidence/oom/after/monitor-34.log), [Before ps](../evidence/oom/before/ps.txt), [After ps](../evidence/oom/after/ps.txt), [실행 전 환경](../evidence/oom/before/preflight.txt), [종료 후 상태](../evidence/oom/before/postflight.txt).

## 3. Root Cause Analysis (원인 분석)

**관측 사실:** 앱의 Heap 카운터는 약 3.05초마다 25MB씩 늘었고 RSS도 약 25MiB씩 증가했다. 해제 없이 데이터가 누적되는 누수와 유사한 누적 패턴이다. 다만 아래 512MB 추가 검증에서 캐시 해제가 관측되어, 영구적인 미해제 결함으로 단정하지 않는다. 앱의 Heap 카운터가 설정 한도에 도달하자 MemoryGuard가 종료를 선언하고 실제 SIGKILL 종료가 뒤따랐다.

**OS 원리:** 프로세스의 가상 주소 공간에는 코드, 힙, 스택, 매핑 영역 등이 있다. 힙에 할당된 데이터에 접근하면 물리 페이지가 사용되며 RSS에 반영될 수 있다. RSS는 상주 페이지 전체를 포함하므로 앱의 Heap 카운터와 동일하지 않다. 증가가 계속되면 다른 프로세스가 쓸 메모리가 줄고 회수·스왑 부담이 커질 수 있다. RSS 정의는 [Linux /proc 공식 문서](https://www.kernel.org/doc/html/v6.15/filesystems/proc.html)를 참고했다.

**종료 원인:** 이번 장애는 **애플리케이션 MemoryGuard의 메모리 보호 종료**이다. 앱 로그, SIGKILL 종료 상태, cgroup OOM 카운터 0이 이를 뒷받침한다. OS 전체 메모리 고갈에 따른 OOM Killer 동작으로 분류하지 않는다.

**확인 한계:** 디컴파일·역공학 없이 실행 결과만 분석했다. 어떤 자료구조가 참조를 유지하는지, 의도적인 캐시인지 코드 결함인지는 확정할 수 없다. 마지막 1초 샘플 이후 메모리를 할당하고 즉시 종료하므로 관제 최대 RSS는 종료 순간의 최고 RSS가 아니다. Heap 100/200MB 로그와 93.09/193.07MiB 관제값의 차이를 모순으로 보지 않았다.

## 4. Workaround & Verification (조치 및 검증)

| 비교 항목 | Before | After |
| --- | --- | --- |
| MEMORY_LIMIT | 100MB | 200MB |
| 나머지 기능 설정 | CPU=40, MULTI=false | CPU=40, MULTI=false |
| 시작 KST / 작업 PID | 15:48:14.648 / 33 | 15:48:48.467 / 34 |
| 생존 시간 | 11.412초 | 23.687초 |
| 최초 → 마지막 RSS | 18,512 → 95,324KiB | 18,480 → 197,708KiB |
| 종료 직전 앱 Heap | 100MB | 200MB |
| 종료 결과 | MemoryGuard / SIGKILL | MemoryGuard / SIGKILL |
| 관측자가 중지했는가 | 아니오 | 아니오 |
| 결과 원본 | [Before](../evidence/oom/before/result.json) | [After](../evidence/oom/after/result.json) |

실험별 로그·키·업로드 경로는 증거 격리를 위해 다른 디렉터리를 사용하고 내용·권한은 같게 유지했다. 변경한 기능 변수는 MEMORY_LIMIT 하나이다.

생존 시간이 **12.275초 증가해 약 2.08배**가 되었다. 같은 증가 패턴과 동일 종료가 계속되므로 한도 상향은 시간을 벌어주는 임시 조치이다. 정상화 또는 누수 해결로 보고하지 않는다. 근본 조치로 할당·참조 유지 지점의 소스 리뷰, 캐시 상한·수명 설정, 힙 프로파일링 후 장시간 RSS 안정화 검증을 제안한다.

### 추가 검증: MEMORY_LIMIT=512에서 정리 경로 확인

같은 CPU=40, MULTI=false인 [cpu/after 실행](../evidence/cpu/after/result.json)을 MEMORY_LIMIT=512의 추가 검증으로도 활용했다. 75.093초까지 자체 종료가 없었고, 15:50:46.822에 `Memory Usage Reached Limit (525MB). Starting cleanup...`, 이어 `Memory Cache Flushed. Process Stabilized.`와 `MEMORY RECOVERED (Cache Cleared)`가 출력되었다. [관제 원본](../evidence/cpu/after/monitor-2443.log)의 RSS는 15:50:46 **528,672KiB**에서 15:50:47 **16,752KiB**로 감소했다.

따라서 100→200MB는 종료 지연에 그쳤으나, **512MB 설정에서는 이 관측 구간에 정상 캐시 정리 경로가 동작**했다. 낮은 한도에서 누적 중 보호 종료되는 설정 의존 동작이 직접적인 장애 원인이다. 내부 분기 기준을 역공학하지 않았으므로 모든 중간 설정의 동작이나 장기 무누수까지 일반화하지 않는다. 임계치 상향 자체가 코드를 수정한 것은 아니다.
