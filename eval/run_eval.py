"""Compare original/current with identical fixed inputs. No external model or tracing."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, default=ROOT)
parser.add_argument('--output', type=Path, default=ROOT / 'eval/results/current-fixed.json')
args = parser.parse_args()
source = args.source.resolve()
if not (source / 'agent/graph.py').is_file():
    parser.error('--source must contain agent/graph.py')
sys.path.insert(0, str(source))
os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
from harness import run_case


async def main():
    cases = json.loads((ROOT / 'eval/cases.json').read_text())
    results = [await run_case(case, source) for case in cases]
    output = {
        'kind': 'fixed-data / existing fake models; not real LLM quality or speed',
        'source': str(source),
        'graph_sha256': hashlib.sha256((source / 'agent/graph.py').read_bytes()).hexdigest(),
        'python': platform.python_version(),
        'langgraph': importlib.metadata.version('langgraph'),
        'case_sha256': hashlib.sha256((ROOT / 'eval/cases.json').read_bytes()).hexdigest(),
        'fixture_sha256': hashlib.sha256((ROOT / 'tests/fixtures/monitoring.json').read_bytes()).hexdigest(),
        'repeat_count': 1, 'warmup': 'not applicable: no real model',
        'timing_note': 'graph contains an artificial 0.35-second task delay; not performance evidence',
        'passed': sum(r['passed'] for r in results), 'total': len(results), 'results': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(f"{output['passed']}/{output['total']} fixed scenarios meet all expected checks -> {args.output}")


asyncio.run(main())
