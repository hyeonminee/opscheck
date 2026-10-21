"""Fixed monitoring/MCP responses; existing fake models, not an LLM quality benchmark."""
import copy
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FixedMCP:
    def __init__(self, source, case):
        self.source, self.case = source, case
        self.monitoring = json.loads((ROOT / 'tests/fixtures/monitoring.json').read_text())
        self.monitoring.update(copy.deepcopy(case.get('overrides', {})))
        self.calls = []

    async def find_prompt(self, name):
        return 'ops'

    async def get_prompt(self, name, arguments, server):
        return '모니터링을 확인하고 해당 매뉴얼을 조회한 뒤 근거로 답하세요.'

    async def find_resource_template(self, name):
        return 'ops', 'runbook://incident-response/{incident_type}'

    async def call_tool(self, name, arguments):
        target = arguments['system_name']
        self.calls.append(target)
        if target in self.case.get('fail_targets', []):
            raise ValueError('controlled lookup failure')
        return copy.deepcopy(self.monitoring.get(target, {
            'system_name': target, 'status': 'unknown', 'cpu_percent': 0.0,
            'memory_percent': 0.0, 'alerts': ['System not found'],
            'last_checked': '2026-10-20T00:00:00+00:00'}))

    async def read_resource(self, uri, server):
        name = uri.rsplit('/', 1)[-1]
        path = self.source / 'data/runbooks' / (name + '.md')
        return path.read_text() if path.exists() and name not in self.case.get('missing_documents', []) else '# Runbook Not Found'


async def run_case(case, source):
    # The caller chooses the import path before importing this module's agent dependencies.
    from agent.deps import SupervisorDeps
    from agent.fake_llm import FastLLM, QualityLLM
    from agent.graph import build_graph
    mcp = FixedMCP(source, case)
    deps = SupervisorDeps(FastLLM('fixed-fake-fast', 0.00015),
                          QualityLLM('fixed-fake-quality', 0.005), mcp, None, 'v2')
    started = time.perf_counter()
    try:
        result = await build_graph(deps).ainvoke({
            'query': case['query'], 'history': case.get('history', []),
            'thread_id': 'fixed-eval', 'events': [], 'cost_usd': 0.0})
        events = result.get('events', [])
        documents = sorted({e['target'] for e in events
                            if e['type'] == 'TaskCompleted' and e['source'] == 'runbook'})
        expected_targets = set(case['expected_targets'])
        actual_targets = set(mcp.calls)
        checks = {
            'route': result.get('route') == case['expected_route'],
            'target_coverage': expected_targets.issubset(actual_targets),
            'no_extra_targets': not (actual_targets - expected_targets),
            'required_runbooks': set(case['expected_runbooks']).issubset(documents),
            'no_extra_runbooks': not (set(documents) - set(case['expected_runbooks'])),
            'completed': bool(result.get('final')),
            'valid_document_sources': all(not c['data'].startswith('# Runbook Not Found')
                                          for c in result.get('collected', []) if c['source'] == 'runbook'),
        }
        if 'expected_reply_contains' in case:
            checks['memory'] = case['expected_reply_contains'] in result.get('final', '')
        return {'id': case['id'], 'checks': checks, 'passed': all(checks.values()),
                'actual_route': result.get('route'), 'targets': mcp.calls,
                'runbooks': documents, 'validation_attempts': result.get('validation_attempts', 0),
                'elapsed_seconds': round(time.perf_counter() - started, 4)}
    except Exception as error:
        return {'id': case['id'], 'passed': False, 'error': type(error).__name__,
                'targets': mcp.calls, 'elapsed_seconds': round(time.perf_counter() - started, 4)}
