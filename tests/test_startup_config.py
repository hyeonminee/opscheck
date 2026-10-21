"""Run launch scripts only in a temporary copy with fake executables; no real ports/keys."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupConfigTests(unittest.TestCase):
    def test_env_models_reach_checks_warmup_and_server(self):
        for script, prefix in [('run_agent_ollma.sh', 'OLLAMA'), ('run_agent_openai.sh', 'OPENAI')]:
            for use_defaults in (False, True):
                with self.subTest(script=script, defaults=use_defaults), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / 'sh').mkdir()
                    shutil.copy2(ROOT / 'sh' / script, root / 'sh' / script)
                    bindir = root / 'bin'; bindir.mkdir()
                    defaults = ('qwen3:0.6b', 'qwen3:8b') if prefix == 'OLLAMA' else ('gpt-4o-mini', 'gpt-4o')
                    fast, quality = defaults if use_defaults else ('configured-fast', 'configured-quality')
                    content = 'OPENAI_API_KEY=unit-test-placeholder\nLANGCHAIN_TRACING_V2=false\n'
                    if not use_defaults:
                        content += f'{prefix}_FAST_MODEL={fast}\n{prefix}_QUALITY_MODEL={quality}\n'
                    (root / '.env').write_text(content)
                    stubs = {
                        'curl': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CHECK_LOG"\nprintf \'{"status":"ok"}\\n\'\n',
                        'ollama': f'#!/bin/sh\nprintf "%s\\n" "{fast} abc" "{quality} def"\n',
                        'lsof': '#!/bin/sh\nexit 1\n',
                        'python3': '#!/bin/sh\nif [ "$1" = server.py ]; then printf "server:%s:%s\\n" "${OLLAMA_FAST_MODEL:-${OPENAI_FAST_MODEL:-default}}" "${OLLAMA_QUALITY_MODEL:-${OPENAI_QUALITY_MODEL:-default}}" >> "$CHECK_LOG"; else cat; fi\n'}
                    for name, value in stubs.items():
                        p = bindir / name; p.write_text(value); p.chmod(0o755)
                    env = {k: v for k, v in os.environ.items() if not k.startswith(('OLLAMA_', 'OPENAI_', 'FAST_MODEL', 'QUALITY_MODEL'))}
                    env.update(PATH=str(bindir) + ':' + env['PATH'], CHECK_LOG=str(root / 'calls.log'))
                    result = subprocess.run(['bash', str(root / 'sh' / script)], input='y\n',
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f'fast={fast} / quality={quality}', result.stdout)
                    calls = (root / 'calls.log').read_text()
                    if not use_defaults:
                        self.assertIn(f'server:{fast}:{quality}', calls)
                    if prefix == 'OLLAMA':
                        self.assertIn(f'"model":"{fast}"', calls)
                        self.assertIn(f'"model":"{quality}"', calls)

    def test_missing_env_and_missing_model_stop_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'sh').mkdir(); bindir = root / 'bin'; bindir.mkdir()
            shutil.copy2(ROOT / 'sh/run_agent_ollma.sh', root / 'sh/run_agent_ollma.sh')
            env = {**os.environ, 'PATH': str(bindir) + ':' + os.environ['PATH']}
            result = subprocess.run(['bash', str(root / 'sh/run_agent_ollma.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('.env', result.stdout)
            (root / '.env').write_text('OLLAMA_FAST_MODEL=missing-configured\nOLLAMA_QUALITY_MODEL=missing-quality\n')
            for name, content in {'curl': '#!/bin/sh\nexit 0\n', 'ollama': '#!/bin/sh\nexit 0\n'}.items():
                p = bindir / name; p.write_text(content); p.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'sh/run_agent_ollma.sh')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('missing-configured 미설치', result.stdout)


if __name__ == '__main__':
    unittest.main()
