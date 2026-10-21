# Phase 0 결과 — 환경 준비와 기준선 확보

완료일: 2026-10-20 · 다음 단계: Phase 1 조사 대상·대화 연결 개선

> **로컬 모델과 브라우저 채팅을 사용할 수 있습니다.** 개선 5개·추가 기능 4개의 구현이 끝난 것은 아닙니다. 이번에는 환경과 테스트 기준을 준비하고 모델 설정 순서만 수정했습니다.

## 1. 완료한 일

| 항목 | 결과 |
|---|---|
| 프로젝트 가상환경 | `.venv` 생성·필요 패키지 설치·`pip check` 통과. Python 3.14.7 |
| 로컬 설정 | `.env.example`와 키 없는 `.env` 생성. 실제 `.env`는 권한 600, Git 제외 규칙 추가 |
| Ollama 모델 | `qwen3:0.6b` 약 522 MB 다운로드. 기존 `qwen3:8b` 재사용 |
| 모델 설정 오류 | 두 `run_agent_*.sh`가 `.env`를 읽은 뒤 모델 변수를 정하도록 수정 |
| 실제 실행 | 백엔드 8100·프론트엔드 8600 기동, 두 `/health` 응답 일치 |
| 실제 브라우저 | 인사 → 답변, payments 장애 → 계획·모니터링·CPU 매뉴얼·작성·종료 확인 |
| 원본 보존 | `baseline/original/`에 코드·스크립트·데이터 29개와 SHA-256 manifest 보존. 비밀 설정·대화 DB는 포함하지 않음 |
| 고정 테스트 | 기존 질문과 추가 실패/유형 사례 21개, 고정 관측값·UTC 시각·fake 모델 사용 |
| 회귀 검사 | 일반 검사 5개 통과, 기존 결함 재현 8개는 예상 실패로 기록. 예기치 않은 실패 없음 |
| 설치 버전 보존 | `requirements-phase0.lock.txt`에 이번 설치 목록 저장. 기존 `requirements.txt`는 보존 |

`setup_ollama.sh`는 기존 Ollama 프로세스를 종료하므로 이번에는 기존 실행이 없는 것을 확인한 뒤 `ollama serve`와 `ollama pull qwen3:0.6b`로 필요한 준비만 수행했습니다. 모델 전체 초기화·기존 모델 삭제는 하지 않았습니다. 백엔드 최초 기동 전 기존 `session.db`가 없는 것도 확인했습니다.

## 2. 테스트 결과를 읽는 법

| 검사 | 결과 | 해석 |
|---|---|---|
| 모델 설정 회귀 | 원본은 사용자 지정 모델 검사·표시 사례에서 실패, 수정 후 통과 | 환경변수 기본값·`.env` 지정·미설치 안내를 실행 대역으로 검사 |
| 기존 결함 8개 | 요청 밖 대상, 복수 매뉴얼 누락, 정상 대상에 DB 매뉴얼, 잘못된 CPU 답변 합격, 짧은 세 번째 초안 강제 합격, 없는 문서 충분 판정, 부분 실패 전체 중단, 미등록 대상 조회 | 아직 고치지 않은 문제를 숨기지 않고 재현. 수정 단계에서 `expectedFailure`를 제거하고 일반 합격 테스트로 전환 |
| 고정 시나리오 | 원본 4/21, Phase 0 현재 4/21 | fake 모델의 규칙 한계도 포함. **실제 모델 정확도·과제 점수가 아님.** 진단 로직을 아직 고치지 않아 결과는 동일 |
| 실제 로컬 모델 | 두 모델을 사용하는 브라우저 요청 2건 완료 | 연결·처리 종료 확인. 다른 모든 질문의 품질을 검증한 결과는 아님 |
| 실제 진단의 오류 | “로그 분석 결과 특이 패턴 없음”, 지속 여부 확인 없이 확장 필요 표현 | 로그 조사 근거가 없고 관측은 한 번뿐. Phase 4의 근거·조치 조건 검증에 사용할 실제 실패 사례 |

고정 평가에서는 기존 `SupervisorDeps`에 고정 MCP 응답을 주입합니다. 외부 모델을 호출하지 않고, 모니터링 임의 변동과 시각도 고정합니다. 기존 코드의 인위적 작업 지연 0.35초가 남아 있으므로 이 시간으로 실제 성능 개선을 주장하지 않습니다. 실모델은 `temperature=0`, thinking·생성량은 모델 기본값이며 컨텍스트는 Ollama 시작 로그의 자동 기본값 4096입니다. 정확한 품질·속도 비교는 Phase 7에서 같은 설정과 반복 횟수로 진행합니다.

## 3. 앞으로 공유할 최소 데이터 계약

아래는 Phase 2~4에서 적용할 약속입니다. 현재 진단 결과가 이미 이 구조로 저장되는 것은 아닙니다. 기존 이름을 우선 유지하고 불필요한 새 저장 계층은 만들지 않습니다.

| 자료 | 최소 필드 | 처리 원칙 |
|---|---|---|
| 관측 | `system_name`, `last_checked`, `ok`, `status`, `cpu_percent`, `memory_percent`, `alerts`, `error` | 미조회·실패 수치는 null. 0이나 정상으로 대체하지 않음. 시각은 시간대 포함 |
| 문서 | `doc_id`, `uri`, `incident_type`, `related_systems`, `content`, `ok`, `error` | 어떤 서비스와 조치의 근거인지 연결. 없는 문서는 성공 자료에서 제외 |
| 조회 실패 | `target`, `kind`, `error_type`, `message`, `retry_count` | 성공한 다른 자료를 보존. 사용자 안내에 키·내부 민감 정보 제외 |
| 최종 보고서 | `run_id`, `thread_id`, 관측·문서·실패, 보고서, `validation_status` | 검증 상태는 pending/rejected/passed/unverified. 최종 저장·비교·내보내기에 같은 데이터 사용 |

관측·문서의 성공 여부와 실패 표현은 Phase 2~3에서 먼저 적용합니다. 보고서 전체는 Phase 4, 조사 저장은 Phase 5에서 구현합니다.

## 4. 다시 확인하는 명령

프로젝트 루트 `/Users/hyeonmin/workspace/AI mini-project/`에서 실행합니다.

```bash
# 외부 API 없이 회귀 검사
.venv/bin/python3 -m unittest discover -s tests -v

# 같은 테스트 장치로 원본과 현재 비교
.venv/bin/python3 eval/run_eval.py --source baseline/original --output eval/results/original-fixed.json
.venv/bin/python3 eval/run_eval.py --output eval/results/phase0-fixed.json
```

현재 로컬 서버는 실행 중입니다. 다시 켤 필요가 있을 때는 터미널 A에서 `bash sh/run_agent_ollma.sh`, 서버 준비 후 터미널 B에서 `bash sh/run_frontend.sh`를 실행합니다. **현재 백엔드 기동 스크립트는 DB를 삭제하므로 기존 대화가 있으면 보존해야 합니다.** 일반 기동 시 DB 삭제 제거는 Phase 1 작업입니다. 실행 중인 서버에 같은 스크립트를 중복 실행하지 않습니다.

## 5. 사용자가 할 일

1. 기존에 노션에 보관한 개선 전 캡처를 유지하세요. 코드 변경 전 [캡처·수치 증거 체크리스트](BEFORE_AFTER_EVIDENCE.md)를 확인하고, 빠진 모델 정보나 재시작 전후 화면만 보완하면 됩니다. 현재 답변의 사실 일치가 보장되는 상태는 아닙니다.
2. 일정은 확인됐습니다: **10월 21일 18:00 제출, 같은 날 09:00~18:00 작업 가능**. [개발 계획의 시간표](DEVELOPMENT_PHASE_PLAN.md)를 기준으로 진행합니다. 별도 발표 시각과 Google Sheet 등록 상태는 확인이 필요합니다.
3. OpenAI·LangSmith를 사용할 단계가 오면 본인 계정의 키를 **로컬 `.env`에만** 넣어야 합니다. 지금 로컬 실습에는 필요하지 않습니다. 이번에는 유료 API 호출·외부 추적을 수행하지 않았습니다.

## 6. 결과 파일

기존 원본·JSON·로그를 보존하고 새로운 측정에는 별도 파일명을 사용합니다. `4/21`은 fake 기반 고정 시나리오 결과이고 `13개 검사 중 8개 예상 실패`는 기존 결함 재현입니다. 실모델 정확도나 개선 후 성과로 바꾸어 해석하지 않습니다.

- [실제 로컬 모델 검증](../eval/results/local-smoke.json): 모델 digest·패키지·설정·답변·확인한 결함
- [회귀 검사 로그](../eval/results/phase0-unittest.txt)
- [원본 고정 평가](../eval/results/original-fixed.json) · [Phase 0 고정 평가](../eval/results/phase0-fixed.json)
- [고정 시나리오](../eval/cases.json) · [관측 fixture](../tests/fixtures/monitoring.json)
- [원본 manifest](../eval/baseline_manifest.json) · [설치 버전](../requirements-phase0.lock.txt)

![실제 Ollama 모델의 브라우저 진단 확인](verification/phase0-browser.png)
