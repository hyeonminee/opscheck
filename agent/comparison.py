"""저장된 두 관측의 차이를 계산한다. 복구 판정·시계열 추론은 하지 않는다."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .runbook_selector import valid_percent

LOCAL_ZONE = ZoneInfo('Asia/Seoul')


def observation_for(record, system):
    for item in record.get('collected', []):
        data = item.get('data')
        if (item.get('source') == 'monitoring' and item.get('ok') is not False
                and isinstance(data, dict) and data.get('system_name') == system
                and data.get('status') in ('healthy', 'degraded', 'down')):
            return data
    return None


def _time(value):
    if not isinstance(value, str):
        return None, False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, False
    assumed = parsed.tzinfo is None
    return (parsed.replace(tzinfo=LOCAL_ZONE) if assumed else parsed), assumed


def compare_service(system, current, previous):
    prior = previous['observation'] if previous else None
    result = {'system_name': system, 'status': 'unavailable', 'previous_run_id': previous['run_id'] if previous else None,
              'previous_checked': prior.get('last_checked') if prior else None,
              'current_checked': current.get('last_checked') if current else None,
              'interval_seconds': None, 'metrics': {}, 'service_status': None, 'alerts': None, 'limitations': []}
    if current is None:
        result['limitations'].append('현재 모니터링 조회 실패 또는 상태 미확인')
    if prior is None:
        result['limitations'].append('같은 세션·서비스의 이전 성공 관측 없음')
    if current is None or prior is None:
        return result
    before, assumed_before = _time(prior.get('last_checked'))
    now, assumed_now = _time(current.get('last_checked'))
    if before is None or now is None:
        result['limitations'].append('관측 시각이 없거나 읽을 수 없어 비교 불가')
        return result
    if now <= before:
        result['limitations'].append('현재 관측 시각이 이전보다 늦지 않아 비교 불가')
        return result
    result['interval_seconds'] = round((now - before).total_seconds(), 3)
    if assumed_before or assumed_now:
        result['limitations'].append('시차 정보가 없는 수업 MCP 시각은 한국 시간(Asia/Seoul)으로 해석')
    result['status'] = 'compared'
    for field in ('cpu_percent', 'memory_percent'):
        old, value = prior.get(field), current.get(field)
        if valid_percent(old) and valid_percent(value):
            result['metrics'][field] = {'previous': old, 'current': value, 'delta_pp': round(value - old, 4)}
        else:
            result['metrics'][field] = {'previous': old if valid_percent(old) else None,
                                        'current': value if valid_percent(value) else None, 'delta_pp': None}
            result['limitations'].append(f'{field}: 이전 또는 현재 유효 수치 없음')
            result['status'] = 'partial'
    result['service_status'] = {'previous': prior['status'], 'current': current['status']}
    old_alerts, current_alerts = prior.get('alerts'), current.get('alerts')
    if (isinstance(old_alerts, list) and isinstance(current_alerts, list)
            and all(isinstance(a, str) for a in old_alerts + current_alerts)):
        old_set, current_set = set(old_alerts), set(current_alerts)
        result['alerts'] = {'added': sorted(current_set - old_set), 'resolved': sorted(old_set - current_set),
                            'unchanged': sorted(old_set & current_set)}
    else:
        result['limitations'].append('알림 목록이 없어 발생·해소 비교 불가')
        result['status'] = 'partial'
    return result


def compare_record(record, store, before_run_id=None):
    return [compare_service(system, observation_for(record, system),
                store.previous_observation(record['thread_id'], system, before_run_id))
            for system in record.get('targets', [])]


def render_comparison(comparisons):
    if not comparisons:
        return ''
    lines = ['이전·현재 비교 · 두 관측의 차이이며 복구 확정이 아닙니다.']
    for item in comparisons:
        lines.append(f"\n{item['system_name']}")
        lines.append(f"- 이전 조회: {item.get('previous_checked') or '미확인'} / 현재 조회: {item.get('current_checked') or '미확인'}")
        if item.get('previous_run_id'):
            lines.append(f"- 비교한 이전 조사: {item['previous_run_id']}")
        if item.get('interval_seconds') is not None:
            lines.append(f"- 관측 간격: {item['interval_seconds']}초")
        if item['status'] == 'unavailable':
            lines.append('- 비교 불가: ' + ' / '.join(item['limitations']))
            continue
        for field, label in [('cpu_percent', 'CPU'), ('memory_percent', '메모리')]:
            metric = item['metrics'][field]
            if metric['delta_pp'] is None:
                lines.append(f'- {label}: 수치 비교 불가')
            else:
                lines.append(f"- {label}: {metric['previous']}% → {metric['current']}% ({metric['delta_pp']:+g}%p)")
        statuses = item['service_status']
        lines.append(f"- 상태: {statuses['previous']} → {statuses['current']}")
        if item['alerts'] is not None:
            for field, label in [('added', '새 알림'), ('resolved', '해소 알림'), ('unchanged', '유지 알림')]:
                lines.append(f"- {label}: " + (' / '.join(item['alerts'][field]) or '없음'))
            lines.append('- 알림은 문자열의 추가·삭제 비교이며 실제 장애 발생·해소 확정이 아닙니다.')
        lines.extend('- ' + issue for issue in item['limitations'])
    return '\n'.join(lines)
