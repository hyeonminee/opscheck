"""서비스별 관측에서 확인할 매뉴얼을 선택한다. 조치 실행·원인 확정은 하지 않는다."""
import math
import re

# shortcut: 현재 6종 런북과 활성 알림 표현에 맞춘 규칙. 문서가 늘면 검색 방식과 비교한다.
RULES = [
    ('cpu-spike', r'\bcpu\b|CPU\s*(?:급증|사용률)'),
    ('memory-leak', r'memory|heap|oom|메모리|힙'),
    ('high-latency', r'latency|connection pool|응답\s*지연|커넥션\s*풀'),
    ('network-timeout', r'timeout|packet loss|network|타임아웃|패킷\s*손실|네트워크'),
    ('disk-full', r'disk|디스크'),
    ('db-down', r'(?:\bdb\b|database|데이터베이스).*(?:down|unavailable|refused|중단|실패)'),
]


def valid_percent(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def valid_runbook(body):
    return isinstance(body, str) and bool(body.strip()) and not body.lstrip().lower().startswith('# runbook not found')


def select_runbooks(collected, targets):
    observations = {c['data']['system_name']: c['data'] for c in collected
                    if c.get('source') == 'monitoring' and isinstance(c.get('data'), dict)
                    and c['data'].get('system_name') and c.get('ok', True)}
    decisions = []
    for target in targets:
        data = observations.get(target, {})
        status = data.get('status')
        issues, reasons = [], {}
        if status not in ('healthy', 'degraded', 'down'):
            issues.append('상태 관측을 확인하지 못함')
        for field in ('cpu_percent', 'memory_percent'):
            if not valid_percent(data.get(field)):
                issues.append(f'{field} 관측을 확인하지 못함')
        alerts = data.get('alerts')
        if not isinstance(alerts, list) or any(not isinstance(a, str) for a in alerts):
            issues.append('활성 알림을 확인하지 못함')
            alerts = []
        # Unknown status is not an observation of a healthy or failed system.
        if status in ('healthy', 'degraded', 'down'):
            for kind, pattern in RULES:
                matches = [a for a in alerts if re.search(pattern, a, re.I)]
                if matches:
                    reasons[kind] = ['활성 알림: ' + a for a in matches]
            for field, threshold, kind in [('cpu_percent', 80, 'cpu-spike'),
                                            ('memory_percent', 90, 'memory-leak')]:
                value = data.get(field)
                if valid_percent(value) and value >= threshold:
                    reasons.setdefault(kind, []).append(f'{field}={value}% (문서 선택 기준 {threshold}% 이상)')
        if reasons:
            decision = 'needs_manual'
        elif status == 'healthy' and not issues and not alerts:
            decision = 'normal'
        else:
            decision = 'insufficient'
            issues.append('관측된 증상에 대응하는 매뉴얼 유형을 확인하지 못함')
        decisions.append({'system_name': target, 'status': decision,
                          'incident_types': list(reasons), 'reasons': reasons, 'limitations': issues})
    return decisions
