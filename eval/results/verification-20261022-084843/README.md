# OpsCheck 최종 검증 증거

- 기준 커밋: 4257de1e3536417bf9ea0af235f70a3b6801475b
- 주 보고서: [검증 보고서](../../../doc/FINAL_EVALUATION_20261022_084843.md)
- 7장·280초 대본: [발표 초안](../../../doc/PRESENTATION_DRAFT_20261022_084843.md)
- 실행 전 기준: criteria/ · 코드/환경/원본 hash: manifest-start.json
- 자동 검사: automatic-summary.json · commands/ · logs/ · test-inventory.json
- 고정35+연결4: validation.json / validation-cases.csv
- SSE26사례29턴·auth5경계/누락: integration/
- 허용된 새 실모델3건/API8회/10,652토큰: live/ (재실행 금지 ledger 포함)
- 전후33개/버전: before-components.* / after-components.* / before-after-comparison.*
- 실시간/다운로드/전후 조건: browser/ · captures/ · capture-index.md
- 기존 외부 추적 익명화: langsmith/ · 과거 auth 실패 익명화: historical/
- 기존 제출물 최신성 검사: submission/
- 임시 도구: tools/ · 임시 합성 데이터 DB: runtime/ 및 live/synthetic-only.sqlite3

데이터셋·통합 검사·구성요소·실제 모델 결과의 분모는 서로 다르므로 합산 정확도로 사용하지 않는다. API키·사용자 대화 원문·.env·사용자 DB는 복사하지 않았다. 이번 임시 DB에는 검증용 합성 질문/관측과 허용된 새 모델 결과만 있다.
