"""Catch syntax errors in physical entrypoints not imported by unit fixtures."""
from pathlib import Path

import pytest


@pytest.mark.parametrize('name', [
    'scripts/persistent_reference_sequence.py',
    'scripts/flexible_fixture_pilot.py',
    'ch3/execution/multiobject_scene.py',
])
def test_continuous_entrypoint_compiles(name):
    path = Path(__file__).resolve().parents[1] / name
    compile(path.read_text(), str(path), 'exec')
