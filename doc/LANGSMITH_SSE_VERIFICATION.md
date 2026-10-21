# LangSmith SSE 요청 추적 보완

2026-10-22 · 기존 Phase 0~10 기록을 보존한 후속 수정.

## 문제와 수정

POST `/chat`는 `chat_request`로 추적되는 `_handle_chat()`에서 실행·저장했다. SSE `/chat/stream`는 별도 생성기에서 실행해 저장 시 요청 컨텍스트가 없고 `langsmith_run_id`가 null이었다. 그래프 내부 추적과 요청/조사 기록 연결을 구분해야 한다.

`server.py`의 실제 SSE 생성기 반복 안에 `async with trace('chat_request', ...)`를 두어 그래프 실행·스트림 생성·결과 저장까지 같은 요청 컨텍스트에서 처리한다. 노드·LLM·MCP 호출은 그 요청 추적 아래 연결된다. 저장한 조사 기록과 `FinalAnswer.langsmith_run_id`에 같은 추적 ID를 전달한다. 앱의 `run_id`는 기존 조사 ID로 유지하며 LangSmith ID와 구분한다.

요청 추적 출력에는 조사 ID·분기·처리 상태·검증 상태를 기록한다. 내부에서 처리한 실패도 timeout/processing_error 같은 안전한 오류 코드로 추적에 남긴다. 취소 예외는 전파해 추적을 종료하고 기존처럼 대화·조사 이력에 완료 저장하지 않는다. 추적 설정이 꺼졌으면 ID는 null이며 외부 기록이 존재한다고 표시하지 않는다.

## 검증

신규 `tests/test_sse_tracing.py` 6개:

| 검사 | 확인 내용 |
|---|---|
| POST/SSE 실제 MCP 계층 | 실제 stdio MCP+fake로 요청 아래 노드·LLM·Tool·Resource·Prompt가 연결되고 기록 ID와 동일 |
| 오류 종료 | 실패 요청의 추적 ID·종료 상태 유지, SSE 오류/FinalAnswer/DONE 계약 보존 |
| 시간 초과·취소 | timeout 추적 연결, 취소된 추적 종료·조사 미저장·컨텍스트 정리 |
| 추적 비활성 | 저장·최종 이벤트 ID가 null, 가짜 추적 ID 없음 |
| 동시 SSE | 각 요청의 추적 ID·대화 맥락 분리, 종료 후 컨텍스트 누출 없음 |
| 활성 SDK의 API 경로 | ASGI `/chat/stream`에서 생성·종료 payload의 요청 ID 일치. 전송 함수는 테스트 대역 |

수정 전 신규5개 중4개 실패로 누락을 재현했다. 수정 후 보강한6개가 통과했다. 전체 Python125개·Node 회귀·고정 검증셋35개 통과. 기존 실제 HTTP/프록시 실시간 도착 검사도 계속 통과한다. 화면 표시 변경은 없고 기존 SSE 필드·종료 계약을 유지한다.

```bash
.venv/bin/python -B -m unittest discover -s tests -p test_sse_tracing.py -v
.venv/bin/python -B -m unittest discover -s tests -v
node tests/test_web_session.cjs
```

SDK local tracing과 전송 함수 대역을 사용하고 실제 HTTP 전송을 차단했다. 유료 모델 호출·LangSmith 외부 업로드·사용자 DB 변경은 없다. 로컬 검사 성공을 외부 LangSmith 수집 성공으로 표현하지 않는다.

## 사용자가 외부 화면에서 확인할 때

1. 로컬 `.env`에서 본인 LangSmith 키·프로젝트 설정을 확인한다. 실제 키를 Git에 넣지 않는다.
2. 외부 추적을 사용하려면 `LANGSMITH_TRACING=true`, `LANGCHAIN_TRACING_V2=true`를 설정한 뒤 백엔드를 재시작한다. 이번 작업에서는 기존 false 설정을 바꾸지 않았다.
3. 화면에서 서비스 진단을 실행하고 LangSmith 프로젝트의 `chat_request`를 연다. transport=sse와 하위 그래프·모델·MCP 호출을 확인한다.
4. `GET /investigations/{run_id}?thread_id=...`의 langsmith_run_id가 해당 요청 ID와 같은지 확인한다. 외부 전송 완료까지의 지연은 수집 환경에 따른다.

## 한계

외부 대시보드 수집·인증·네트워크·표준 토큰/비용 집계는 별도 확인이 필요하다. 기존 기록의 null ID를 소급 생성하지 않는다. 사용 중인 백엔드는 자동 재시작하지 않았으며 새 코드를 반영하려면 재시작해야 한다. RAG나 Multi-Agent 구조는 이번 변경 범위가 아니다.
