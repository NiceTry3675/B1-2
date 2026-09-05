# 실험 증거 목록

모두 제공 바이너리의 실제 실행 결과이며 예시 수치를 사용하지 않았다. 실행 시간은 2026-09-05 KST이다. 앱/monitor는 KST, JSON/50ms CSV/ps 스냅샷 헤더는 UTC(+9시간=KST)이다.

| 폴더 | 기능 설정 MEMORY / CPU / MULTI | 실제 작업 PID | 결과 |
| --- | --- | --- | --- |
| oom/before | 100 / 40 / false | 33 | 11.412초 SIGKILL |
| oom/after | 200 / 40 / false | 34 | 23.687초 SIGKILL |
| cpu/before-02 | 512 / 100 / false | 1089 | 30.504초 Watchdog SIGTERM |
| cpu/after | 512 / 40 / false | 2443 | 75.093초 관측 후 정리 |
| deadlock/before | 512 / 40 / true | 5782 | 순환 대기, 60.063초 후 정리 |
| deadlock/after | 512 / 40 / false | 8441 | 작업 진행, 60.356초 후 정리 |
| discovery/initial | 100 / 100 / false | 31 | 탐색, MemoryGuard 종료 |
| cpu/before | 512 / 100 / false | 31 | 탐색, 마지막 터미널 print 유실로 PTY 재실험 |

각 실행 폴더의 파일:

- `settings.json`: 시작 시간, 실행 명령, 환경변수, 부모 PID, 관측 제한
- `preflight.txt`: 일반 사용자, OS, 메모리, 포트, 바이너리 해시. 정식 실험은 cgroup 상한·OOM 카운터도 기록
- `app.log`: PTY 표준 출력·오류 (탐색 2회는 일반 리다이렉션)
- `application-logs/agent_app.log`: 앱이 직접 생성한 로그 원본 복사
- `monitor-<PID>.log`: 부모와 실제 작업 자식의 1초 CPU/RSS를 각각 기록
- `cpu-burst-<PID>.csv`: CPU 정식 사례에만 추가한 약 50ms CPU 샘플
- `ps.txt`, `top.txt`: 5초 간격 PID·스레드 출력 및 ps.txt의 app.log 바이트 수
- `result.json`: 최종 생존/관측 시간, Python 종료 상태, 관측자 종료 여부
- `postflight.txt`: 종료 후 프로세스와 소켓, 정식 실험의 cgroup OOM 카운터

`returncode=-9`는 SIGKILL, `-15`는 SIGTERM이다. 쉘의 관례적 137/143 표기가 아닌 Python Popen의 신호 표기다. `observer_stopped=true`인 -15는 예정된 실험 정리다. 생존 시간은 부모 프로세스 시작부터 회수와 관측기 정리가 완료될 때까지의 monotonic 경과 시간이므로 작은 계측 오버헤드가 포함된다.

`summary.json`은 `scripts/summarize.py`가 정식 6회의 작업 자식 PID를 기준으로 계산한 파생 통계다. 좀비 샘플은 제외한다. 앱 내부 Heap/Load 값과 OS RSS/CPU의 의미를 구분한다. 컨테이너에서 보이는 전체 메모리 약 16GiB와 cgroup 한도 1GiB도 서로 다른 수치이다.

`container-hostconfig.json`과 `image-inspect.json`은 실제 실행 컨테이너 설정 및 이미지 정보를 보존한다. 비교 실험은 동시에 실행하지 않았다. OOM Before는 독립 컨테이너, 나머지 정식 5회는 같은 설정의 컨테이너에서 순차 실행했다. 재현 스크립트는 매번 새 컨테이너를 사용한다.

원본 무결성은 `SHA256SUMS`로 확인할 수 있다. PDF에 첨부된 `evidence.zip`에도 원본을 포함했다. PDF 내부 링크의 상대 경로는 함께 제공된 프로젝트 폴더를 기준으로 한 참조이다.

## FAIL #15 추가 진단 증거

`deadlock/diagnostic-01/`은 2026-09-05 16:28~16:29 KST의 별도 60초 실행이다. 앱 PID 35(analyst), 부모 16, 워커 LWP 331/332이며 기능 설정은 512/40/true이다. GDB/strace가 타이밍에 영향을 줄 수 있어 기존 6회 비교 통계에 합산하지 않았다.

- `strace-futex-write.txt`: LWP별 write 및 futex 원문; `strace-attach.txt`: attach/detach 출력
- `gdb-thread-bt-01.txt`, `gdb-thread-bt-02.txt`: 약 15초 간격의 전체 스레드 12프레임 backtrace
- `proc-during-strace.txt`, `proc-after-gdb-01.txt`, `proc-after-gdb-02.txt`: syscall, wchan, LWP, 앱 로그 크기
- `diagnostic-events.json`, `diagnostic-tools.txt`: 시각·명령·도구 버전과 앱 해시
- 기존 실행기 형식의 app/monitor/ps/top/settings/result/preflight/postflight 파일도 함께 보존

`SHA256SUMS`는 추가 원본까지 포함한다. 검증 결과 문서 `verification.txt`와 `fail-remediation-verification.txt`는 갱신되는 감사 기록이므로 해시 목록에서 제외한다.
