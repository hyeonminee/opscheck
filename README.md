# OpsCheck

**MCP와 LangGraph로 서비스 상태를 점검하고 근거 중심의 장애 진단을 제공하는 학습 프로젝트**입니다.

서비스 이름과 증상을 입력하면 모니터링 정보·대응 매뉴얼을 조회하고, 관측값·분석·권장 조치·미확인 사항·출처로 결과를 정리합니다. 수업에서 제공한 `all_total_vue` 코드를 기반으로 개선했습니다.

> 모니터링은 수업 샘플 데이터입니다. 실제 장애 원인·복구를 확정하거나 운영 조치를 자동 실행하지 않습니다.

![OpsCheck 첫 화면](doc/verification/ui-overview.png)

## 주요 기능

- 조사 대상 확인 및 계획 검증: 누락 보완, 중복·범위 밖 조사 제외
- 서비스별 복수 매뉴얼 연결과 정상·정보 부족 구분
- 서비스·수치·출처·조치 단계 검증 및 제한된 재작성
- 부분 조회 실패·시간 초과·생성 한도 안내
- 대화 복원, 후속 질문의 새 조사, 전체/현재 대화 조사 이력·커서 페이지 조회
- 이전·현재 관측 비교(%p·상태·알림), Markdown 보고서 다운로드
- 실제 작업 시작·개별 완료·재시도를 SSE로 표시하고 처리 과정을 저장하는 Vue 화면
- 답변 텍스트 점진 표시: 작성 중 가설은 OpenAI 응답 스트림으로 '검증 전' 초안에 표시하고, 최종 5개 영역 보고서는 검증 후 점진 표시
- 요청·하위 호출·저장된 조사 ID를 하나의 LangSmith 요청 추적으로 연결(추적 설정을 켠 경우)

## 기술 구성

| 영역 | 사용 기술 |
|---|---|
| 서버·그래프 | Python, FastAPI, LangGraph |
| 외부 자료 | MCP Tool·Resource·Prompt |
| 모델 | fake, Ollama, OpenAI 선택 |
| 화면·전달 | Vue 3 CDN, SSE, 동일 출처 프록시 |
| 저장·검증 | SQLite, Pydantic, unittest·Node 검사, LangSmith 추적(선택) |

## 실행

Python 가상환경과 의존성을 먼저 준비합니다. 모델 백엔드는 `LLM_BACKEND`로 고르며 기본값은 결정론적 `fake`, 선택지는 `ollama`(로컬 실모델)·`openai`(실제 API)입니다. 아래 순서는 Ollama 기준이며 이 모드에서는 Ollama 설치와 모델 준비가 필요합니다.

```bash
bash sh/setup_venv.sh
cp .env.example .env
bash sh/setup_ollama.sh
```

예제 설정을 참고해 본인 환경을 로컬 `.env`에만 입력합니다. 예제 파일과 Git에 실제 키를 넣지 마세요.

```bash
# 터미널 A: 로컬 모델 백엔드
bash sh/run_agent_ollma.sh

# 터미널 B: 화면
bash sh/run_frontend.sh
```

브라우저에서 `http://localhost:8600`을 엽니다. 백엔드 상태는 `http://localhost:8100/health`에서 확인합니다. 기동 스크립트는 해당 포트의 기존 프로세스를 종료할 수 있으므로 다른 서비스가 사용 중인지 확인하세요. 대화 DB는 유지합니다.

OpenAI를 쓰려면 `.env`에 본인 키·모델을 설정하고 터미널 A에서 `bash sh/run_agent_openai.sh`를 사용합니다. 실제 API 비용이 발생합니다. OpenAI 모드에는 Ollama 모델 준비가 필요 없습니다.

### 질문 예시

```text
payments-service 장애 진단해줘
그 서비스 다시 확인해줘
payments-service와 cache-service를 함께 확인해줘
```

조사 후 **조사 이력 → 기록 선택 → Markdown 내려받기**로 저장한 보고서를 확인합니다. 첫 조사에는 비교할 이전 기록이 없습니다.

## 검증

```bash
.venv/bin/python -B -m unittest discover -s tests -v
node tests/test_web_session.cjs
.venv/bin/python -B eval/run_final_eval.py --source baseline/original --output /tmp/opscheck-before.json
.venv/bin/python -B eval/run_final_eval.py --output /tmp/opscheck-after.json
```

2026-10-22 로컬 검증에서 Python 130개와 Node 화면 검사, 검증셋 자동 판정 35개(연결 계약 4개는 Python 테스트에 포함)를 통과했습니다. 전후 비교는 동일 노드 입력·고정 관측을 사용하며 실제 모델 전체 정확도나 속도 개선율을 뜻하지 않습니다. 검증기의 자유형/JSON 계약 차이는 결과 보고서에 명시했습니다.

| 고정 검사 | 원본 | 개선 |
|---|---:|---:|
| 대상 계획 일치 | 1/7 | 7/7 |
| 매뉴얼 집합 일치 | 11/15 | 15/15 |
| 오류 초안 차단 | 0/7 | 7/7 |
| 올바른 초안 오차단 | 0/2 | 0/2 |

실제 OpenAI + 실제 MCP로 진단 3건을 실행한 결과, 2건은 진단을 완료했고 auth-service 복합 증상 1건은 선택된 CPU 매뉴얼의 조치가 두 번 모두 빠져 검증 미통과로 종료했습니다(안전하게 차단했지만 답변 완성은 실패). 3건의 실제 사용 토큰은 10,652개이며 청구액은 측정하지 않았습니다. 소수 사례이므로 전체 정확도로 해석하지 않습니다. 자세한 내용은 [최종 평가 검증 보고서](doc/FINAL_EVALUATION_20261022_084843.md)에 있습니다.

### 개선 전·후 LangSmith 실측

실제 OpenAI·MCP로 같은 세 질문(단일 → 같은 대화의 후속 → 복수)을 각 1회 실행한 요청 루트입니다. 상세는 [개선 전](doc/LANGSMITH_BEFORE_MEASUREMENT_20261022_110948.md)·[개선 후](doc/LANGSMITH_AFTER_MEASUREMENT_20261022.md) 문서와 `eval/results/langsmith-*` 원시 결과에 있습니다. LangSmith 링크는 비공개 계정이라 싣지 않고 프로젝트 이름·시각·trace ID만 남겼습니다.

| 질문 | 개선 전 | 개선 후(세트 B) |
|---|---|---|
| 단일 `payments-service가 느려요` | 5.50초 · 922토큰 | 3.26초 · 2,119토큰 |
| 후속 `그 서비스 다시 확인해줘` | 2.11초 · 418토큰, **MCP 조회 없이 이전 수치 재사용** | 3.39초 · 2,135토큰, **새로 조회** |
| 복수 `payments-service와 cache-service를 확인해줘` | 6.63초 · 1,086토큰, **cpu-spike만 조회(memory-leak 누락)** | 6.01초 · 3,394토큰, cpu-spike·memory-leak 모두 조회 |

기능 차이(후속 재조회, 서비스별 매뉴얼)가 이 비교의 핵심입니다. 시간·토큰은 같은 조건의 속도·비용 개선율이 아닙니다. 개선 전은 측정용 래퍼 루트와 OpenAI 응답 usage, 개선 후는 앱의 요청 루트와 어댑터 기록 토큰이고, 복수 질문은 개선 전 새 대화·개선 후 같은 대화였고, MCP 샘플 값이 요청마다 달라지며, 단일 요청 토큰은 오히려 늘었습니다.

## 문서·발표

- [실행 메인 매뉴얼](doc/README.md)
- [최종 결과 보고서](doc/FINAL_REPORT.md)
- [프로젝트 이해 가이드](doc/PROJECT_GUIDE.md)
- [설계 결정과 한계](doc/ADR.md)
- [최종 평가 검증 보고서](doc/FINAL_EVALUATION_20261022_084843.md) · [개선 전후 증거](doc/BEFORE_AFTER_EVIDENCE.md)
- 최종 발표: [발표 PPT(21장)](submission/OpsCheck_최종발표_수정본_20261022.pptx) · [5분 발표 대본](submission/OpsCheck_발표대본_5분.md)
- 이전 버전(Phase 7 시점): [발표 PPT](submission/AI_Mini_Project_Presentation.pptx) · [발표 대본](doc/PRESENTATION_SCRIPT.md) · [이전 발표 초안](doc/PRESENTATION_DRAFT_20261022_084843.md)
- [개선 전 LangSmith 실측](doc/LANGSMITH_BEFORE_MEASUREMENT_20261022_110948.md) · [개선 후 LangSmith 실측](doc/LANGSMITH_AFTER_MEASUREMENT_20261022.md)
- [커밋 메시지·작업 지침](AGENTS.md)

`baseline/original/`은 개선 전 수업 코드입니다. 개인 개발 로그·대화 DB·설정·생성 ZIP·예전 수업 캡처는 공개 파일에서 제외했습니다. 과거 단계 문서의 개발 로그 참조는 공개 저장소에서 열리지 않을 수 있습니다. 화면은 아래 최신 UI를 기준으로 확인합니다. ‘이전 버전’으로 표시한 발표 대본·PPT는 Phase 7 시점(Python 107개, 실모델 3건 통과) 기준이라 최신 결과(130개, 실모델 2건 완료·1건 실패)와 다릅니다. 최신본은 위의 최종 발표 두 파일입니다.

![진단 보고서와 조사 이력](doc/verification/ui-report.png)

## 한계

- 문서 선택은 6종 매뉴얼에 대한 규칙 기반이며 임베딩·BM25 RAG가 아닙니다.
- 검증 통과는 구조·근거 연결 검사를 통과했다는 뜻이며 가설 의미 전체를 보장하지 않습니다.
- 두 관측 비교만 제공하며 추세·복구·재발을 판정하지 않습니다.
- SQLite 단일 서버 실습이며 `thread_id`는 사용자 인증 수단이 아닙니다.
- v1은 레거시 경로로, v2와 같은 개선 기능을 제공하지 않습니다.
- auth-service 같은 복합 증상에서 선택된 모든 매뉴얼의 조치를 작성하지 못해 검증 미통과로 끝날 수 있습니다. 최종 평가에서 실제 모델로 재현했고, 수정안은 제안만 하고 적용하지 않았습니다.

## 후속 개발 기록

- [Phase 8 검증셋](doc/VALIDATION_DATASET.md): 정답 검증셋 39개(자동 판정 35·연결 계약 4)
- [Phase 9 전체 이력](doc/PHASE9_RESULT.md): 인증 없는 개인 실습용으로 같은 DB의 기록을 통합 조회
- [Phase 10 실시간 진행](doc/PHASE10_RESULT.md): 실제 작업 단계에 연결한 진행 상태
- [LangSmith SSE 요청 추적](doc/LANGSMITH_SSE_VERIFICATION.md): 요청·하위 호출·저장 조사 ID 연결. 외부 대시보드 수집은 별도 확인이 필요합니다.
- [답변 스트리밍 검증](doc/ANSWER_STREAMING_VERIFICATION.md)

위 문서의 이전 시점 수치(Python 107·119·125개 등)는 당시 기록으로 보존했고, 현재 기준은 130개입니다.
