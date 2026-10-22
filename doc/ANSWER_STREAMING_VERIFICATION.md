# 답변 텍스트 스트리밍 보완 검증 — 2026-10-22

## 요청과 원인

채팅의 답변이 완성된 뒤 통째로 표시되던 동작을, 글이 이어서 나타나는 방식으로 변경했다.
OpenAI 어댑터는 완성 응답을 한 번만 콜백에 보냈고, 최종 보고서는 FinalAnswer.reply를 화면에 한 번에 대입했다.
기존 초안/반려/통과/미통과 구분과 최종 검증은 유지한다.

## 동작과 변경 파일

| 파일 | 변경 |
|---|---|
| agent/openai_llm.py | 콜백을 사용하는 대화·작성은 실제 HTTP SSE delta를 즉시 전달. 최종 빈 choices의 usage와 stop/[DONE] 확인. 분류·계획 등 완성 응답 경로 유지 |
| agent/graph.py | JSON 원문은 숨기고 hypothesis의 복호화 가능한 접두사를 검증 전 초안으로 점진 전달. 분할 escape·유니코드 대기, 중복 방지 |
| web_chat.html | 수신 텍스트와 표시 텍스트를 분리해 프레임마다 점진 표시. 최종 보고서는 검증 후 시작. 오류 시 대기 출력 폐기, 새 대화 후 중단, 읽던 스크롤 유지, reduced-motion이면 즉시 표시 |
| tests/test_answer_streaming.py | 차단한 HTTP 스트림에서 완료 전 도착, chat/write, UTF-8 분할·usage, 잘림·불완전 종료, 오류·시간 초과·취소, 초안 escape·중복 검사 5개 |
| tests/test_chat_messages.py | 기존 역할/이력 검사 대역을 OpenAI SSE 계약으로 갱신 |
| tests/test_web_session.cjs | 결정론적 프레임 시계로 점진 표시·최종 문자열 일치·오류 폐기·reduced-motion·부분 보고서 레이아웃 검사 |

**구분:** 초안은 실제 모델 생성 스트림이다. 최종 5개 영역 보고서는 구조·수치·출처 검증 후 코드로 조립하므로 완성된 보고서를 브라우저에서 점진 표시한다. 최종 보고서 표시 속도를 실제 모델 생성 속도·지연 개선 수치로 해석하지 않는다. 단계 Progress는 기존처럼 실제 작업 상태에 연결되며 표시 연출 타이머로 바꾸지 않았다.

## 실행한 검사

- [x] `.venv/bin/python -B -m unittest discover -s tests -v`: 130개 통과. 로컬 TCP/프록시 검사는 승인된 네트워크 환경에서 실행.
- [x] `node tests/test_web_session.cjs`: 통과.
- [x] `.venv/bin/python -B eval/run_validation.py --output <임시 경로>/opscheck-answer-stream-validation.json`: 자동 판정 35 통과, 실패 0, 연결 계약 4. 연결 계약은 위 전체 테스트에 포함.
- [x] 작업 해제 전 토큰 도착: Event로 다음 조각을 차단한 상태에서 첫 텍스트를 소비하고 실행 미종료 확인. 최종 이벤트 순서만 본 검사가 아님.
- [x] 실제 브라우저: 임시 18100/18600 서버, 고정 MCP·임시 DB·로컬 OpenAI 호환 SSE 대역. 단일/복수 서비스, 반려→재작성→pending→passed, 보고서 5개 영역, 전체 이력 2건 확인.
- [x] 390×844 화면에서 작성 중 가설 일부가 나타난 상태와 완료 보고서 확인. 페이지 폭=뷰포트 폭=390, 임시 뷰포트 복원.
- [x] `git diff --check`: 통과.

화면: `<임시 경로>/opscheck-answer-stream-desktop.jpg`, `<임시 경로>/opscheck-answer-stream-mobile-live.jpg`, `<임시 경로>/opscheck-answer-stream-mobile-complete.jpg`.
로컬 서버/대역은 확인용이며 프로젝트에 새 의존성이나 테스트용 API를 추가하지 않았다.

## 기존 실제 LangSmith 기록 조회

새 모델 호출 없이 기존 사용자 진단 3건을 SDK로 읽었다. 모두 저장된 langsmith_run_id=실제 chat_request ID, trace 출력 run_id=조사 ID였고 하위 실행 미종료/실행 오류는 0건이었다. model plan/write·MCP Tool/Resource/Prompt·LangGraph 노드가 같은 trace에 존재했다. Chrome 상세 트리에서도 cache-service 요청과 MCP/모델 호출 및 passed 출력을 확인했다. 인앱 브라우저의 특정 상세 열기에서는 LangSmith 자체 오류 화면이 나왔으나 Chrome/SDK 조회는 성공했다.

**추적 성공과 진단 품질은 별개다.** 조회 3건 중 cache 1건은 passed, auth 후속 질문 2건은 unverified였다. 저장된 최근 auth 3건에는 `doc:cpu-spike` 필수 조치 누락이 있었고 일부에는 가설 수치/확정 표현 반려 사유도 있었다. 기존 결과를 수정하거나 강제 통과시키지 않았다. 이 답변 품질은 별도 보완 대상으로 남긴다. LangSmith의 총 토큰/비용 집계 정확성은 이번에 보완하지 않았다.

## 직접 확인

화면을 새로고침하고 `payments-service 결제 응답이 느려요. 원인을 확인해줘.`를 보낸다. 작성 중에는 검증 전 초안이 이어서 나타나고, 최종 검증 후 보고서가 순서대로 표시된다. 반려 시 해당 초안은 최종 답변이 아니라고 표시한다. 이력 상세는 저장된 완성 보고서를 바로 표시한다.

## 한계

실제 유료 OpenAI API의 변경 후 스트리밍은 새로 호출하지 않았다. 실제 HTTP 어댑터는 로컬 SSE 대역으로 검증했고 외부 LangSmith 확인은 변경 전 사용자 요청을 조회했다. 초안은 아직 검증 전이므로 최종 권고로 해석하면 안 된다. 운영 프록시/네트워크에 따라 조각이 묶여 도착할 수 있다. 원본·사용자 DB·비밀 설정·기존 제출물은 보존한다.

참고: [OpenAI 공식 Chat 스트리밍 계약](https://developers.openai.com/api/reference/resources/chat) — delta·include_usage·finish_reason.

## 재시작 확인

변경 후 백엔드를 같은 OpenAI/v2 설정으로 재시작했고 8100 health 및 8600의 최신 HTML을 확인했다. 기존 채팅 화면을 새로고침해 대화 복원과 입력 가능 상태를 확인했다. 재시작 전후 .env·session.db 해시가 같고 새 유료 모델 호출은 0회다.
