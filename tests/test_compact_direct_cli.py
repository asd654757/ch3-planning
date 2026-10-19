"""The preferred paired protocol must never accidentally include legacy feedback."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def invoke(*flags):
    return subprocess.run([sys.executable, 'scripts/flexible_fixture_pilot.py',
        '--output-dir', '/unused/preflight', '--env-file', '/unused/private', *flags],
        cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT)},
        capture_output=True, text=True)


def test_compact_direct_requires_execution_contract():
    result = invoke('--compare-compact-direct')
    assert result.returncode == 2
    assert 'require --execution-contract' in result.stderr


def test_compact_direct_rejects_legacy_comparison():
    result = invoke('--compare-compact-direct', '--compare-feedback-formats')
    assert result.returncode == 2
    assert 'cannot include legacy' in result.stderr
