# 장애 진단 보고서

- 조사 ID: 4206bb43-0e86-4ba6-8931-d037b0cf08ba
- 질문: 그 서비스 다시 확인해줘 [비교 2]
- 저장 시각: 2026-10-22T00:01:06.342423+00:00
- 처리 상태: completed
- 검증 상태: passed
- 데이터: 수업 샘플 데이터

검증 통과

## 1. 관측값
- payments-service: 상태=degraded, CPU 71.0%, 메모리 50.0%, 조회=2026-10-20T00:01:00+00:00 [obs:payments-service]
  활성 알림(발생 시점 미확인): CPU 91% (threshold 80%) / payments-service-3 instance responding slow

## 2. 분석
- payments-service 가설(미확정): 관측된 지표와 증상의 관련성을 추가 확인할 필요가 있습니다. / 관측 status=degraded, cpu_percent=71.0, memory_percent=50.0 [obs:payments-service]

## 3. 권장 조치
- payments-service 조건부 제안: 조건·지속 여부 미확인. 해당 조건을 확인한 후에만 검토. get_monitoring_status로 CPU 사용률과 최근 배포/트래픽 변화 시점을 확인한다. [obs:payments-service, doc:cpu-spike; 단계 1]

## 4. 미확인 사항
- 단일 관측이므로 지속 시간·복구·공통 원인을 확정할 수 없습니다.
- 활성 알림의 발생 시각은 제공되지 않아 알림 수치와 현재 측정값의 시점 일치를 확인하지 못했습니다.

## 5. 출처
- [obs:payments-service] get_monitoring_status · payments-service
- [doc:cpu-spike] cpu-spike.md · runbook://incident-response/cpu-spike

## 이전·현재 비교

이전·현재 비교 · 두 관측의 차이이며 복구 확정이 아닙니다.

payments-service
- 이전 조회: 2026-10-20T00:00:00+00:00 / 현재 조회: 2026-10-20T00:01:00+00:00
- 비교한 이전 조사: 0e539678-70ae-447d-86b0-c13afbbc48c0
- 관측 간격: 60.0초
- CPU: 91.0% → 71.0% (-20%p)
- 메모리: 55.0% → 50.0% (-5%p)
- 상태: degraded → degraded
- 새 알림: 없음
- 해소 알림: 없음
- 유지 알림: CPU 91% (threshold 80%) / payments-service-3 instance responding slow
- 알림은 문자열의 추가·삭제 비교이며 실제 장애 발생·해소 확정이 아닙니다.
