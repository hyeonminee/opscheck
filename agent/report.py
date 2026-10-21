"""수집 자료로 5개 영역 보고서를 만들고 구조화된 모델안의 근거를 검사한다."""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError
from .runbook_selector import valid_percent, valid_runbook


class Claim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: str
    field: Literal['status', 'cpu_percent', 'memory_percent']
    value: StrictFloat | StrictInt | StrictStr


class Analysis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system_name: str
    hypothesis: str = Field(min_length=3, max_length=300)
    evidence_ids: list[str] = Field(min_length=1)
    claims: list[Claim] = Field(min_length=1)


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system_name: str
    document_id: str = Field(description='문서 근거 id: doc:cpu-spike 같은 doc: 접두사 ID. cpu-spike.md 파일명이 아님')
    step_index: int = Field(ge=1, strict=True)
    evidence_ids: list[str] = Field(min_length=2)
    condition_verified: Literal[False]


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analysis: list[Analysis] = Field(min_length=1)
    actions: list[Action]


def evidence_catalog(collected):
    sources = {}
    for item in collected:
        data = item.get('data')
        if item.get('source') == 'monitoring' and item.get('ok') is not False and isinstance(data, dict):
            system = data.get('system_name')
            if not system:
                continue
            known = data.get('status') in ('healthy', 'degraded', 'down')
            observation = {key: data.get(key) for key in ('system_name', 'status', 'last_checked')}
            for field in ('cpu_percent', 'memory_percent'):
                observation[field] = data.get(field) if known and valid_percent(data.get(field)) else None
            observation['alerts'] = data.get('alerts') if isinstance(data.get('alerts'), list) else []
            sources['obs:' + system] = {'id': 'obs:' + system, 'kind': 'monitoring',
                'system_name': system, 'usable': known, 'tool': 'get_monitoring_status', 'data': observation}
        elif item.get('source') == 'runbook' and item.get('ok') is not False and valid_runbook(data):
            incident = item.get('incident_type') or item.get('target') or item.get('doc_id', '').removesuffix('.md')
            if not incident:
                continue
            steps = {int(n): text.strip() for n, text in re.findall(r'^\s*(\d+)\.\s+(.+)$', data, re.M)}
            source_id = 'doc:' + incident
            sources[source_id] = {'id': source_id, 'kind': 'runbook', 'doc_id': item.get('doc_id', incident + '.md'),
                'uri': item.get('uri'), 'related_systems': item.get('related_systems', []),
                'selection_reasons': item.get('selection_reasons', {}), 'data': data, 'steps': steps}
    return sources


def writer_prompt(query, collected, feedback):
    system = ('당신은 한국어 운영 진단 보고서 작성자다. 필드·ID를 제외한 설명은 한국어로 작성하라. JSON 객체만 반환하라. analysis와 actions만 작성한다. '
        '관측값·출처·미확인 사항은 코드가 표시한다. analysis는 사용 가능한 각 관측 서비스마다 작성한다. '
        'hypothesis는 미확정 가설이며 숫자·퍼센트·다른 서비스명을 쓰지 말고, 수치/상태는 claims에만 적는다. '
        'claims의 field/value는 같은 서비스의 관측값을 그대로 사용한다. 분석 문장에 지속·동시 발생·복구·원인 확정을 단정하지 마라. '
        'actions의 document_id에는 evidence 안의 id 값(doc: 접두사, 예 doc:cpu-spike)을 그대로 넣어라. doc_id 파일명(cpu-spike.md)을 넣지 마라. 실제 steps의 번호 하나를 step_index에 선택하고 연결된 관측·문서 ID를 evidence_ids에 포함한다. '
        '문서의 조건·지속 여부는 단일 관측으로 검증할 수 없으므로 condition_verified는 반드시 false다. '
        '없는 출처·미조회 서비스·알림 문장의 수치를 현재 관측값으로 사용하지 마라. 매뉴얼이 없으면 actions는 빈 배열이다.')
    user = json.dumps({'query': query, 'evidence': evidence_catalog(collected),
        'limitations': [c for c in collected if c.get('source') in ('runbook_failure','monitoring_failure','runbook_selection')],
        'analysis_context_unverified': [c['data'] for c in collected if c.get('source') == 'analysis'],
        'output_schema': Draft.model_json_schema(), 'feedback': feedback}, ensure_ascii=False)
    return system, user


def validate_draft(raw, collected, requested_targets):
    sources = evidence_catalog(collected)
    try:
        draft = Draft.model_validate_json(raw)
    except ValidationError as error:
        return None, ['보고서 형식을 읽지 못했습니다.' if e['type'] == 'json_invalid' else '보고서 항목 누락 또는 형식 불일치: ' + '.'.join(map(str, e['loc'])) for e in error.errors()[:8]]
    errors = []
    usable = {v['system_name'] for v in sources.values() if v['kind'] == 'monitoring' and v['usable']}
    seen = set()
    for entry in draft.analysis:
        system = entry.system_name
        obs_id = 'obs:' + system
        if system not in requested_targets or system not in usable:
            errors.append(f'{system}: 요청 범위 또는 성공 관측 없음')
            continue
        seen.add(system)
        if obs_id not in entry.evidence_ids:
            errors.append(f'{system}: 관측 근거 {obs_id} 누락')
        for source_id in entry.evidence_ids:
            source = sources.get(source_id)
            if not source or source['kind'] != 'monitoring' or source['system_name'] != system:
                errors.append(f'{system}: 다른 서비스 또는 없는 분석 근거 {source_id}')
        # Numeric facts are rendered from typed claims; free text cannot introduce another number.
        text = entry.hypothesis.replace(system, '')
        if re.search(r'\d', text):
            errors.append(f'{system}: 분석 문장의 수치는 claims로 분리해야 함')
        mentioned = set(re.findall(r'\b[a-zA-Z][\w]*(?:-[\w]+)+\b', text))
        if any(name.endswith('-service') or re.fullmatch(r'web-\d+', name) for name in mentioned):
            errors.append(f'{system}: 분석 문장에 다른 서비스 연결 금지')
        if re.search(r'확정됐|확정된 원인|원인은.+입니다|지속되고|지속됩니다|계속 증가하고|복구됐|동시에.+발생', text):
            errors.append(f'{system}: 단일 관측으로 원인·지속·복구를 확정할 수 없음')
        if sources[obs_id]['data'].get('status') != 'healthy' and re.search(r'정상|healthy', text, re.I):
            errors.append(f'{system}: 정상 단정은 관측 상태와 불일치')
        for claim in entry.claims:
            source = sources.get(claim.evidence_id)
            expected = sources[obs_id]['data'].get(claim.field)
            if claim.evidence_id not in entry.evidence_ids or not source or claim.evidence_id != obs_id:
                errors.append(f'{system}: {claim.field} 근거는 {obs_id}여야 함')
            elif expected is None or claim.value != expected:
                errors.append(f'{system}: {claim.field} 기대값={expected!r}, 작성값={claim.value!r}, 근거={obs_id}')
    if usable - seen:
        errors.append('분석 누락 서비스: ' + ', '.join(sorted(usable - seen)))
    for action in draft.actions:
        system = action.system_name
        document = sources.get(action.document_id)
        if system not in requested_targets or system not in usable:
            errors.append(f'{system}: 조치 대상의 성공 관측 없음')
        if not document or document['kind'] != 'runbook':
            errors.append(f'{system}: 없는 조치 문서 {action.document_id}. document_id는 파일명이 아닌 근거 ID여야 함. 사용 가능한 ID: ' + ', '.join(s['id'] for s in sources.values() if s['kind'] == 'runbook'))
            continue
        if system not in document['related_systems']:
            errors.append(f'{system}: 문서 {action.document_id}가 다른 서비스에 연결됨')
        if action.step_index not in document['steps']:
            errors.append(f'{system}: 문서에 없는 조치 단계 {action.step_index}')
        required = {'obs:' + system, action.document_id}
        if not required.issubset(action.evidence_ids):
            errors.append(f'{system}: 조치에 관측·문서 근거가 모두 필요')
        if set(action.evidence_ids) - required:
            errors.append(f'{system}: 조치의 범위 밖 출처')
    required_actions = {(system, source['id']) for source in sources.values() if source['kind'] == 'runbook'
                        for system in source['related_systems'] if system in usable and source['steps']}
    missing_actions = required_actions - {(a.system_name, a.document_id) for a in draft.actions}
    if missing_actions:
        errors.append('매뉴얼 조치 누락: ' + ', '.join(f'{system}/{doc}' for system, doc in sorted(missing_actions)))
    return draft.model_dump(), errors


def build_report(collected, requested_targets, status, draft=None, reason=None):
    sources = evidence_catalog(collected)
    observations = [{**s['data'], 'evidence_id': s['id']} for s in sources.values() if s['kind'] == 'monitoring']
    limits = ['단일 관측이므로 지속 시간·복구·공통 원인을 확정할 수 없습니다.',
              '활성 알림의 발생 시각은 제공되지 않아 알림 수치와 현재 측정값의 시점 일치를 확인하지 못했습니다.']
    if reason:
        limits.insert(0, reason)
    covered = {d['system_name'] for d in observations}
    for system in requested_targets:
        if system not in covered:
            limits.append(f'{system}: 모니터링 미확인')
    for obs in observations:
        for field in ('cpu_percent', 'memory_percent'):
            if obs[field] is None:
                limits.append(f"{obs['system_name']}: {field} 미확인")
        if not sources[obs['evidence_id']]['usable']:
            limits.append(f"{obs['system_name']}: 상태 미확인, 정상으로 판단하지 않음")
    for item in collected:
        if item.get('source') in ('monitoring_failure', 'runbook_failure'):
            limits.append(f"{item.get('target', '')}: {item['data']}")
        if item.get('source') == 'runbook_selection':
            for decision in item['data']:
                limits.extend(f"{decision['system_name']}: {issue}" for issue in decision.get('limitations', []))
    actions = []
    if status == 'passed' and draft:
        for action in draft['actions']:
            document = sources[action['document_id']]
            actions.append({**action, 'condition': '조건·지속 여부 미확인. 해당 조건을 확인한 후에만 검토',
                            'text': document['steps'][action['step_index']]})
    return {'validation_status': status, 'observations': observations,
            'analysis': draft['analysis'] if status == 'passed' and draft else [],
            'actions': actions, 'limitations': list(dict.fromkeys(limits)),
            'sources': [{key: value for key, value in source.items() if key not in ('data', 'steps')}
                        for source in sources.values()]}


def render_report(report):
    lines = ['검증 통과' if report['validation_status'] == 'passed' else '검증 미통과 · 확인한 자료만 안내', '\n1) 관측값']
    for obs in report['observations']:
        values = [f"{label} {obs[key]}%" if obs[key] is not None else f'{label} 미확인'
                  for key, label in [('cpu_percent','CPU'),('memory_percent','메모리')]]
        lines.append(f"- {obs['system_name']}: 상태={obs.get('status')}, {', '.join(values)}, 조회={obs.get('last_checked') or '미확인'} [{obs['evidence_id']}]")
        if obs['alerts']:
            lines.append('  활성 알림(발생 시점 미확인): ' + ' / '.join(map(str, obs['alerts'])))
    if not report['observations']:
        lines.append('- 모니터링 정보를 확보하지 못해 진단할 수 없습니다.')
    lines.append('\n2) 분석')
    for analysis in report['analysis']:
        claims = ', '.join(f"{c['field']}={c['value']}" for c in analysis['claims'])
        lines.append(f"- {analysis['system_name']} 가설(미확정): {analysis['hypothesis']} / 관측 {claims} [{', '.join(analysis['evidence_ids'])}]")
    if not report['analysis']:
        lines.append('- 검증된 분석을 제공하지 않습니다.')
    lines.append('\n3) 권장 조치')
    for action in report['actions']:
        lines.append(f"- {action['system_name']} 조건부 제안: {action['condition']}. {action['text']} [{', '.join(action['evidence_ids'])}; 단계 {action['step_index']}]")
    if not report['actions']:
        lines.append('- 확인된 매뉴얼 조치를 제공하지 않습니다. 추가 자료를 확보해 주세요.')
    lines.append('\n4) 미확인 사항')
    lines.extend('- ' + issue for issue in report['limitations'])
    lines.append('\n5) 출처')
    for source in report['sources']:
        location = (source['doc_id'] + ' · ' + str(source.get('uri'))) if source['kind'] == 'runbook' else source['tool'] + ' · ' + source['system_name']
        lines.append(f"- [{source['id']}] {location}")
    if not report['sources']:
        lines.append('- 확보한 출처 없음')
    return '\n'.join(lines)


def export_markdown(record):
    """저장된 최종 답변·상태·비교만 사용한다. 재조사·모델 호출은 없다."""
    from .comparison import render_comparison
    body = re.sub(r'(?m)^([1-5])\) (관측값|분석|권장 조치|미확인 사항|출처)$', r'## \1. \2', record['reply'])
    lines = ['# 장애 진단 보고서', '', f"- 조사 ID: {record['run_id']}", f"- 질문: {record['query']}",
             f"- 저장 시각: {record['created']}", f"- 처리 상태: {record['processing_status']}",
             f"- 검증 상태: {record['validation_status']}",
             '- 데이터: 수업 샘플 데이터' if record.get('is_sample') else '- 데이터: 원본 기록의 출처를 확인해 주세요.',
             '', body]
    comparison = render_comparison(record.get('comparison', []))
    if comparison:
        lines.extend(['', '## 이전·현재 비교', '', comparison])
    return '\n'.join(lines) + '\n'
