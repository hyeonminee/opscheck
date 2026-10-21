# 정답과 판정 기준을 포함한 검증셋 v1

작성: 2026-10-21 · [검증 데이터](../eval/validation_cases.v1.json) · [후속 계획](DEVELOPMENT_PHASE_PLAN.md#후속-개발-계획--phase-810-2026-10-21-추가)

## 정답은 어떻게 정했나

- 프로그램 응답을 녹화해 정답으로 쓰지 않았다. 요청한 범위·대화 격리·근거 검증 요구와 고정 관측·6종 매뉴얼을 먼저 기준으로 정했다.
- 회귀 문서의 auth 자동 대체·DB 삭제·ID 변경·미구현 표기는 과거 설명이다. 원본 기록은 보존하며 현재 정답은 이 데이터셋을 따른다.
- 예를 들어 `web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘`에서 web은 고정 관측상 healthy/CPU 34/메모리 48이다. 사용자 표현을 수치 근거로 바꾸거나 web에 CPU 매뉴얼을 붙이지 않는다. auth의 지연/풀 알림에는 high-latency 문서를 연결한다.
- payments의 CPU 91은 측정값, 알림 속 80과 매뉴얼 속 90은 기준값이다. 서로 대체하지 않는다. 단일 관측은 지속·원인·복구의 확정 근거가 아니다.
- DB down은 명시적 DB 중단 알림 사례에만 정답이다. 유형을 모르겠다는 이유로 기본 문서로 쓰지 않는다.

## 데이터 계약

| 영역 | 내용 |
|---|---|
| 입력 | 질문 또는 순서 있는 turns, pre_context(history/session_context), 고정 관측 사본 |
| 오류 주입 | fail_targets(조회 실패), missing_documents(문서 없음), 별도 오류 초안 |
| 분기·조회 | branch, targets, 모니터링/문서 성공·실패 목록, 서비스별 manuals_by_service |
| 보고서 | 서비스별 사실·실제 출처 ID·조건/단일 관측/시각 한계와 금지 조회·단정 |
| 종료 | processing_status, validation_status; 오류 초안은 accept=false |
| 판정 | 합격/불합격 기준과 사람이 읽는 예시, 정답 근거. 예시와 동일한 문장은 요구하지 않음 |
| 보존·연결 | 기존 eval ID 전부 연결, 원본 SHA256 고정, Phase 9/10 계약 test_reference |

`manuals_by_service`는 필요한 문서이며, 없는 문서는 documents_failure에 남기고 실제 출처·조치에서 제외한다. unknown 수치는 null로 표시하며 0으로 정상화하지 않는다. 조회 실패는 성공 관측으로 만들지 않는다.

## 사례 목록

| ID | 유형 | 입력/행동 | 정답 핵심 |
|---|---|---|---|
| payments-single | single_target | payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요 | incident: payments-service |
| cache-single | single_target | cache-service 메모리 사용률이 계속 올라가고 있어요 | incident: cache-service |
| log-single | single_target | log-service 디스크 공간이 부족하다는 알림이 왔어요 | incident: log-service |
| gateway-payments | multiple_targets | gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요 | incident: gateway-service, payments-service |
| web-auth | multiple_targets | web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘 | incident: web-01, auth-service |
| payments-cache | multiple_targets | payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘 | incident: payments-service, cache-service |
| unspecified | target_confirmation | 로그인 응답이 느려요, 원인 확인해주세요 | clarify: 조회 없음 |
| unknown-billing | unregistered | billing-service 응답이 느려요 | clarify: 조회 없음 |
| greeting | general_chat | 안녕, 너는 누구야? | chat: 조회 없음 |
| name-first | general_chat | 제 이름은 민수입니다 | chat: 조회 없음 |
| name-followup | general_chat | 제 이름이 뭐였죠? | chat: 조회 없음 |
| healthy-web | normal | web-01 상태 진단해줘 | incident: web-01 |
| partial-failure | partial_failure | payments-service와 cache-service 장애 진단 | incident: payments-service, cache-service |
| type-cpu-spike | single_target | auth-service 장애 진단 | incident: auth-service |
| type-memory-leak | single_target | auth-service 장애 진단 | incident: auth-service |
| type-disk-full | single_target | auth-service 장애 진단 | incident: auth-service |
| type-high-latency | single_target | auth-service 장애 진단 | incident: auth-service |
| type-network-timeout | single_target | auth-service 장애 진단 | incident: auth-service |
| type-db-down | single_target | auth-service 장애 진단 | incident: auth-service |
| unknown-observation | insufficient_data | auth-service 장애 진단 | incident: auth-service |
| missing-runbook | insufficient_data | auth-service 장애 진단 | incident: auth-service |
| clarify-then-target | target_confirmation | 로그인 응답이 느려요 → auth-service요 | clarify: 조회 없음 → incident: auth-service |
| service-followup | followup | payments-service 진단 → 그 서비스 다시 확인해줘 | incident: payments-service → incident: payments-service |
| followup-new-context | followup | 그 서비스 다시 확인해줘 | clarify: 조회 없음 |
| name-dialogue | general_chat | 제 이름은 민수입니다 → 제 이름이 뭐였죠? | chat: 조회 없음 → chat: 조회 없음 |
| all-lookups-fail | partial_failure | payments-service와 cache-service 진단 | incident: payments-service, cache-service |
| draft-valid | correct_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 수용 |
| draft-wrong-value | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-wrong-service | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-invented-source | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-wrong-document | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-missing-step | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-assert-condition | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-assert-recovery | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| draft-missing-analysis | bad_draft | 이 초안의 관측·출처·조치 조건이 근거와 일치하는가? | 반려 |
| history-scope | phase9 | 두 대화 A/B 각각 payments/cache 진단 → 새 대화 → 전체/현재 필터 → 원래 ID 상세·다운로드 | 전체 A/B 모두; 현재는 해당 대화만; 현재 맥락 불변; 저장 비교 불변 |
| history-pagination | phase9 | 동일 시각 25건 → 더 보기 → 사이에 새 조사 → 필터 전환 중 느린 응답 | 커서 스냅샷 순회에서 누락·중복 없음; 늦은 응답 무시 |
| live-progress | phase10 | 느린 cache 조회를 Event로 차단하고 빠른 payments를 종료; 실패/재시도·재작성 주입 | cache 종료 전 running 및 payments 완료 수신; ID별 시도 구분; 최종·DONE 유지 |
| saved-progress | phase10 | 완료/실패 조사 저장 → 새 대화에서 과거 상세; 레거시 기록; 취소 | 저장 상태에 running/pending 없음; 레거시 정상 표시; 취소 기록 미저장 |

## 실행과 해석

```bash
.venv/bin/python -B eval/run_validation.py --output /tmp/opscheck-validation-new.json
.venv/bin/python -B -m unittest discover -s tests -v
node tests/test_web_session.cjs
```

출력 파일이 이미 있으면 덮어쓰지 않고 종료한다. 임시 결과 파일명을 새로 지정한다. 검증기는 fake 모델과 고정 MCP를 사용하고 사용자 DB·유료 API를 사용하지 않는다. dialogue는 순서대로 history/context를 전달해 실제 그래프를 실행한다.

자동 판정은 분기·정확한 조회 대상/결과·서비스별 문서 집합·관측 필드·출처·조치 연결·조건 미확인·최종 상태를 검사한다. HTTP 200이나 답변 존재는 합격 기준이 아니다. bad_draft는 정답 초안 1개와 의미별 오류 초안 8개를 구분해 실제 검증기에 입력한다. 오류 초안만 반려하는지와 정답 오차단을 함께 확인한다.

가설 의미의 타당성, 충분한 설명·한계 전달은 사람이 예시/근거를 보고 검토해야 한다. 자동 결과에 manual_semantic_review=not_automated를 표시한다. fake 통과를 실제 LLM 정확도·품질 점수로 발표하지 않는다.

## Phase 8 확인 결과

- 39사례: 그래프/대화 26개, 초안 판정 9개, 후속 통합 계약 4개.
- 고정 그래프·초안 자동 판정: 35개 통과, 실패 0개.
- 원본 SHA256, 기존 21개 ID·질문 포함 및 필드 계약 검사를 실행한다.
- Phase 9·10의 4개 계약은 이 시점에는 linked_test이며 기능 완료로 세지 않는다. 이후 해당 Phase의 결과 문서와 실제 테스트가 근거다.
- 화면 변경은 없으므로 Phase 8에서 새 브라우저 확인이 필요한 동작은 없다.
