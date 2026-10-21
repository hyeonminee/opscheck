# Phase 8 검증셋 결과

2026-10-21. 과거 Phase 0~7 결과는 보존했다.

- 구현: 39개 정답 사례, 근거 SHA256, 요구 기반 판정·사람용 예시·합격/불합격·금지 동작. 기존 21개 ID와 회귀 질문 전부 포함.
- 자동 결과: 고정 그래프/대화 26개 + 정답/오류 초안 9개 = 35개 통과. 후속 통합 계약 4개는 linked_test이며 이 단계에서 완료로 세지 않는다.
- 검사: 전체 Python 110개 통과(기존 107 + 신규 3), Node 화면 회귀 통과.
- 변경: `eval/validation_cases.v1.json`, `eval/run_validation.py`, `tests/test_validation_dataset.py`, `doc/VALIDATION_DATASET.md`, 계획·메인 매뉴얼·누적 개발 로그.
- 화면: 화면 변경 없음. 브라우저 신규 검사 대상 없음.
- 직접 확인: 루트에서 `.venv/bin/python -B eval/run_validation.py --output /tmp/validation-check-new.json`. 이미 있는 출력은 보존하므로 새 이름을 사용한다.
- 한계: fake 결정론적 기능 판정이며 실모델 품질·가설 의미의 정확도는 별도 사람 검토가 필요하다. HTTP 성공/예시 문장 일치는 판정 기준이 아니다. 유료 호출 0, 사용자 DB 변경 없음.
