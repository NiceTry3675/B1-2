# B1-2 시스템 장애 분석 과제

제공 Linux 바이너리를 수정·디컴파일하지 않고 실행하여 장애별 Before/After 6회와 탐색 실행 2회의 증거를 수집했다. 2026-09-05 KST 실측 결과를 GitHub Issue 형식으로 정리했다.

## 학습하기

처음 읽는다면 [주니어 개발자를 위한 학습 가이드](LEARNING_GUIDE.md)부터 시작하세요. 프로세스·메모리·CPU·스레드·락 개념을 실제 장애 결과와 연결하고, 로그 읽기 연습과 확인 문제를 담았습니다.

## 제출 결과물

- **제출용 PDF:** [system-incident-reports.pdf](output/pdf/system-incident-reports.pdf) (3개 이슈, 그래프, 보너스 분석, 원본 증거 ZIP 첨부)
- [OOM / MemoryGuard 보고서](reports/oom.md)
- [CPU / Watchdog 보고서](reports/cpu.md)
- [Deadlock 보고서](reports/deadlock.md)
- [동시 장애 우선순위·대응 절차](reports/incident-runbook.md)
- [보너스: 스케줄링 추론](reports/scheduling.md)
- [원본 증거 안내](evidence/README.md), [통계 JSON](evidence/summary.json)

| 사례 | Before | After | 판정 |
| --- | --- | --- | --- |
| OOM | 100MB, 11.412초 후 SIGKILL | 200MB, 23.687초 후 SIGKILL | 한도 상향은 종료 지연; 512MB 추가 관측에서는 캐시 정리 확인 |
| CPU | 100%, 30.504초 후 Watchdog SIGTERM | 40%, 75.093초 관측 중 자체 종료 없음 | cooldown 경로로 보호 종료 회피 |
| Deadlock | true, 약 9초 뒤 순환 대기 | false, 60.356초 동안 작업 진행 | 문제 동시 트랜잭션 경로 회피 |

75초/60초 관측이 끝난 실행은 관측자가 SIGTERM으로 정리했다. 이 종료를 장애로 세지 않았다. CPU 내부 Load와 OS 사용률은 다른 지표이며, OS CPU 최대치 감소는 확인되지 않았다. 보고서에서 이 차이와 관측 한계를 설명했다.

## 실행 환경과 사전 준비

기존 문서에 언급된 OrbStack `agent-lab` 머신은 현재 머신 목록에 없어 별도 Ubuntu 컨테이너를 생성했다. Docker 이미지 `b1-2-lab`, Ubuntu 22.04.5, x86_64, Linux 6.17.8, 사용자 `analyst`(UID 1000)를 사용했다. 앱 실행은 root가 아니다. 각 컨테이너에 메모리 1GiB, CPU 상한 2코어를 설정하고 외부 네트워크를 차단했다. 내부 0.0.0.0:15034 바인딩은 가능하며 호스트에 포트를 게시하지 않았다.

실행기가 매번 다음 환경을 준비하고 설정·부트 결과를 보존한다.

```text
AGENT_HOME=/work/runtime/<case>
AGENT_PORT=15034
AGENT_UPLOAD_DIR=$AGENT_HOME/upload_files
AGENT_KEY_PATH=$AGENT_HOME/api_keys
AGENT_LOG_DIR=$AGENT_HOME/logs
secret.key 내용: agent_api_key_test (권한 600)
```

한 비교 쌍에서는 MEMORY_LIMIT, CPU_MAX_OCCUPY, MULTI_THREAD_ENABLE 중 하나만 변경했다. 실행별 디렉터리는 로그 혼합 방지를 위해 다르지만 같은 내용·권한으로 생성했다. 앱 바이트 SHA-256은 `7e0a19cfa80ece6b547a5008273661f0d4d71e526e96b51e0d0f341dd1bb3e40`이다.

## 재현

Docker가 동작하는 macOS 또는 Linux에서 실행한다. 아래 `-rerun-01`은 기존 증거를 덮어쓰지 않는 새 실행 이름이다. 같은 이름의 폴더가 있으면 다른 접미사를 사용한다.

```bash
chmod +x agent-app-leak/agent-leak-app-*
bash scripts/run_experiments.sh -rerun-01
```

OrbStack CLI가 PATH에 없는 이 Mac에서는 다음과 같이 실행한다.

```bash
DOCKER_BIN="$HOME/.orbstack/bin/docker" bash scripts/run_experiments.sh -rerun-01
```

개별 재현 명령은 각 보고서에 있다. `scripts/run_case.py`는 Linux 표준 라이브러리로 환경을 준비하고 앱을 실행한다. `/proc/PID/exe`와 프로세스 그룹으로 해당 앱의 부모·자식을 찾아 각각 관측한다. 실제 작업 자식 PID를 보고서의 기준으로 삼는다.

기본 실험은 종료 메시지가 버퍼에 남지 않도록 PTY를 사용했다. 첫 탐색 실행 `discovery/initial`, `cpu/before`는 일반 파이프 리다이렉션이어서 마지막 print 메시지가 유실될 수 있다. 이 때문에 PTY로 정식 비교를 다시 수행했으며 탐색 자료도 그대로 보존했다. 표준 출력은 줄바꿈만 CRLF→LF로 정규화했다. 바이너리나 앱 로그 본문은 수정하지 않았다.

## 관측과 검증

- `monitor.sh`: 대상 PID의 약 1초 CPU와 RSS를 기록. CPU는 1코어=100%, RSS_KB는 KiB, MEM은 Linux가 보이는 전체 메모리 대비 비율이다.
- `scripts/sample_cpu.py`: CPU 사례의 약 50ms `/proc` 샘플. 실제 측정 간격을 각 행에 기록하며 tick 양자화 오차가 있다.
- `ps -ef`, `ps -L`, `top -b -H`: PID, 스레드, 대기 지점, 메모리 상태를 5초 간격으로 보존한다. top 첫 프레임과 ps의 CPU 평균을 monitor의 구간 사용률과 혼동하지 않는다.
- `scripts/summarize.py`: 정식 6회 원본에서 통계를 재계산한다. 새 접미사 실험은 분석 대상 CASES를 변경해 별도로 집계한다.
- `scripts/build_pdf.py`: 보고서와 실측 차트를 PDF로 생성하고 증거 ZIP을 PDF에 첨부한다. Python reportlab/pypdf와 한글 TTF 폰트가 필요하다.

```bash
docker run --rm -v "$PWD:/work" b1-2-lab bash -c \
  'shellcheck monitor.sh scripts/run_experiments.sh && python3 -m py_compile scripts/run_case.py scripts/sample_cpu.py scripts/summarize.py'
python3 scripts/summarize.py
```

[검증 기록](evidence/verification.txt)에 정적 검사, 원문 발췌 일치, 링크·수치·PDF 검수 결과를 보존했다. 보너스 결론은 앱의 FCFS와 부합하는 순차 패턴이며 Linux 커널 알고리즘 확정 주장이 아니다. 공개 저장소 [NiceTry3675/B1-2](https://github.com/NiceTry3675/B1-2) 또는 PDF 파일로 제출할 수 있다. PDF는 평가 FAIL #15·#18 보완 내용과 추가 진단 원본을 포함한다.

## AI 사전평가 FAIL 항목 보완

2026-09-05 16:12:29 평가에서 FAIL인 두 항목만 보완했다. 기존 OOM/CPU 비교 실험·통계는 유지했다. 아래 보완의 근거를 검증했으며 자동평가 재실행 결과를 받은 것은 아니다.

| 항목 | 보완 내용 | 근거 |
| --- | --- | --- |
| #15 스택 직접 증거 | GDB 전체 스레드 backtrace 2회, strace write/futex, 디버거 분리 후 syscall 상태로 Worker-1/2 ↔ LWP ↔ 지속 락 대기 연결 | [Deadlock 추가 직접 증거](reports/deadlock.md), [원본 진단](evidence/deadlock/diagnostic-01/) |
| #18 동시 장애 절차 | 영향·자원 고갈·업무 정지 기준 P0/P1/P2, 동시 알림 선택 사례, 증거→격리→재시작→검증·재평가 절차 | [운영 Runbook](reports/incident-runbook.md) |

진단 재현은 `Dockerfile.diagnostics`와 `scripts/capture_deadlock.py`를 사용한다. 앱은 UID 1000을 유지하고 추적 도구에만 격리 컨테이너의 root/SYS_PTRACE 권한을 사용한다. 정확한 명령은 Deadlock 보고서에 있다. [보완 검증 기록](evidence/fail-remediation-verification.txt)을 함께 보존했다.
