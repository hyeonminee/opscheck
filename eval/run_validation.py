"""Requirement-based fixed cases; no live models, user DB, or exact-answer matching."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval')]
os.environ.update(LLM_BACKEND='fake', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.report import validate_draft
from harness import FixedMCP


def load_dataset():
    data = json.loads((ROOT / 'eval/validation_cases.v1.json').read_text())
    ids = [c['id'] for c in data['cases']]
    assert len(ids) == len(set(ids))
    for source in data['sources']:
        assert hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() == source['sha256'], source['path']
    previous = {c['id'] for c in json.loads((ROOT / 'eval/cases.json').read_text())}
    assert previous <= {id for c in data['cases'] for id in c.get('legacy_case_ids', [])}
    return data


def assess(result, mcp, expected):
    """Independent expected values, not the selector/validator as the grading oracle."""
    checks = {'branch': result.get('route') == expected['branch'],
              'targets': result.get('requested_targets', []) == expected['targets'],
              'looked_up_targets': sorted(mcp.calls) == sorted(expected['targets']),
              'processing_status': result.get('processing_status', 'completed') == expected['terminal']['processing_status'],
              'validation_status': result.get('validation_status') == expected['terminal']['validation_status']}
    lookups = expected['lookups']
    items = result.get('collected', [])
    for source, key in [('monitoring','monitoring_success'), ('monitoring_failure','monitoring_failure'),
                        ('runbook','documents_success'), ('runbook_failure','documents_failure')]:
        checks[key] = sorted(c.get('target') for c in items if c.get('source') == source) == sorted(lookups[key])
    if expected['branch'] == 'incident':
        report = result.get('report') or {}
        observations = {o['system_name']: o for o in report.get('observations', [])}
        checks['facts'] = set(observations) == set(expected['facts']) and all(
            all(observations[s].get(k) == v for k,v in facts.items()) for s,facts in expected['facts'].items())
        checks['sources'] = sorted(s['id'] for s in report.get('sources', [])) == sorted(expected['required_sources'])
        selections = next((c['data'] for c in items if c['source']=='runbook_selection'), [])
        checks['service_manuals'] = {s['system_name']: sorted(s['incident_types']) for s in selections} == {
            s: sorted(ds) for s,ds in expected['manuals_by_service'].items()}
        expected_actions = {(s,'doc:'+d) for s,docs in expected['manuals_by_service'].items()
                            for d in docs if d in lookups['documents_success']}
        checks['actions'] = {(a['system_name'],a['document_id']) for a in report.get('actions', [])} == expected_actions
        checks['conditions'] = all(a['condition_verified'] is False and a.get('condition') for a in report.get('actions', []))
        checks['limits'] = bool(report.get('limitations')) and any('단일 관측' in s for s in report['limitations'])
        checks['unverified_safe'] = result.get('validation_status') != 'unverified' or not (report.get('actions') or report.get('analysis'))
        checks['sections'] = all(f'{i}) {title}' in result.get('final','') for i,title in enumerate(
            ['관측값','분석','권장 조치','미확인 사항','출처'],1))
    else:
        checks['no_diagnosis'] = not result.get('report') and not items
        if expected['branch'] == 'clarify':
            checks['confirmation'] = bool(result.get('session_context', {}).get('pending_query')) and '등록된 서비스' in result.get('final','')
    if expected.get('reply_contains'):
        checks['memory'] = expected['reply_contains'] in result.get('final', '')
    return checks


async def run_case(case):
    if case['kind'] == 'integration_contract':
        return {'id':case['id'], 'status':'linked_test', 'test_reference':case['test_reference']}
    if case['kind'] == 'draft_validation':
        _, errors = validate_draft(json.dumps(case['draft'],ensure_ascii=False), case['fixed_evidence'],case['pre_context']['targets'])
        checks = {'accept_matches_oracle': (not errors) == case['expected']['accept']}
    else:
        history = list(case['pre_context']['history'])
        context = dict(case['pre_context']['session_context'])
        checks = {}
        for index, turn in enumerate(case['turns']):
            faults = {**turn['faults'], 'overrides':turn['fixed_observations']}
            mcp = FixedMCP(ROOT, faults)
            deps = SupervisorDeps(FastLLM('validation-fake-fast',0),QualityLLM('validation-fake-quality',0),mcp,None,'v2')
            result = await build_graph(deps).ainvoke({'query':turn['question'],'thread_id':'validation-only',
                'history':history,'session_context':context,'events':[],'cost_usd':0.0})
            checks.update({f'turn{index+1}.{k}':v for k,v in assess(result,mcp,turn['expected']).items()})
            context = result.get('session_context',context)
            history += [{'role':'user','content':turn['question']},{'role':'assistant','content':result['final']}]
    return {'id':case['id'],'status':'passed' if all(checks.values()) else 'failed','checks':checks,
            'manual_semantic_review':'not_automated'}


async def run_all():
    return [await run_case(c) for c in load_dataset()['cases']]


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    results=asyncio.run(run_all())
    payload={'dataset':'validation_cases.v1.json','model':'fake','results':results,
             'passed':sum(r['status']=='passed' for r in results),
             'failed':sum(r['status']=='failed' for r in results),
             'linked_tests':sum(r['status']=='linked_test' for r in results)}
    if args.output:
        if args.output.exists():raise SystemExit('Existing result preserved; choose a new output file')
        args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in payload.items() if k!='results'}))
    for r in results:
        if r['status']=='failed':print(r['id'],[k for k,v in r['checks'].items() if not v])
    raise SystemExit(bool(payload['failed']))
