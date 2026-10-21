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
- 대화 복원, 후속 질문의 새 조사, 조사 이력 저장·조회
- 이전·현재 관측 비교(%p·상태·알림), Markdown 보고서 다운로드
- SSE 진행 상태와 검증 상태를 표시하는 Vue 화면

## 기술 구성

| 영역 | 사용 기술 |
|---|---|
| 서버·그래프 | Python, FastAPI, LangGraph |
| 외부 자료 | MCP Tool·Resource·Prompt |
| 모델 | fake, Ollama, OpenAI 선택 |
| 화면·전달 | Vue 3 CDN, SSE, 동일 출처 프록시 |
| 저장·검증 | SQLite, Pydantic, unittest·Node 검사 |

## 실행

Python 가상환경과 의존성을 먼저 준비합니다. Ollama 모드에서는 Ollama 설치와 모델 준비가 필요합니다.

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

2026-10-21 로컬 검증에서 Python 107개와 Node 검사를 통과했습니다. 전후 비교는 동일 노드 입력·고정 관측을 사용하며 실제 모델 전체 정확도나 속도 개선율을 뜻하지 않습니다. 검증기의 자유형/JSON 계약 차이는 결과 보고서에 명시했습니다.

| 고정 검사 | 원본 | 개선 |
|---|---:|---:|
| 대상 계획 일치 | 1/7 | 7/7 |
| 매뉴얼 집합 일치 | 11/15 | 15/15 |
| 오류 초안 차단 | 0/7 | 7/7 |
| 올바른 초안 오차단 | 0/2 | 0/2 |

## 문서·발표

- [실행 메인 매뉴얼](doc/README.md)
- [최종 결과 보고서](doc/FINAL_REPORT.md)
- [프로젝트 이해 가이드](doc/PROJECT_GUIDE.md)
- [설계 결정과 한계](doc/ADR.md)
- [발표 대본](doc/PRESENTATION_SCRIPT.md) · [발표 PPT](submission/AI_Mini_Project_Presentation.pptx)
- [커밋 메시지·작업 지침](AGENTS.md)

`baseline/original/`은 개선 전 수업 코드입니다. 개인 개발 로그·대화 DB·설정·생성 ZIP·예전 수업 캡처는 공개 파일에서 제외했습니다. 과거 단계 문서의 로컬 경로와 개발 로그 참조는 공개 저장소에서 열리지 않을 수 있습니다. 화면은 아래 최신 UI를 기준으로 확인합니다. PPT의 화면은 UI 변경 전 기능 검증 캡처입니다.

![진단 보고서와 조사 이력](doc/verification/ui-report.png)

## 한계

- 문서 선택은 6종 매뉴얼에 대한 규칙 기반이며 임베딩·BM25 RAG가 아닙니다.
- 검증 통과는 구조·근거 연결 검사를 통과했다는 뜻이며 가설 의미 전체를 보장하지 않습니다.
- 두 관측 비교만 제공하며 추세·복구·재발을 판정하지 않습니다.
- SQLite 단일 서버 실습이며 `thread_id`는 사용자 인증 수단이 아닙니다.
- v1은 레거시 경로로, v2와 같은 개선 기능을 제공하지 않습니다.
