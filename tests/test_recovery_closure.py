from pathlib import Path
from scripts.validate_recovery_closure import mechanism_check, forbidden_paths, isolation_check


def test_completion_gate_single_mechanism_attribution():
    rows = mechanism_check()['cases']
    full = [r for r in rows if r['method'] == 'FULL']
    ablated = [r for r in rows if r['method'] != 'FULL']
    assert sum(r['unsupported_completion'] for r in full) == 0
    assert sum(r['unsupported_completion'] for r in ablated) == 2
    assert all(r['status'] == 'complete' for r in rows if r['condition'] == 'positive_evidence_control')
    assert all(r['model_calls'] == 0 for r in rows)


def test_nested_scoring_leak_is_not_ignored():
    assert forbidden_paths({'history': [{'terminal_scores': {'arrived': True}}]}) == [
        'request.history[0].terminal_scores']


def test_existing_pilot_information_boundary():
    root = Path('data/collections/core_recovery_pilot_20261020_122719')
    result = isolation_check(root)
    assert result['requests_checked'] > 0
    assert result['scored_episodes'] == 20
    assert result['passed']
