import json

import pytest

from scripts.analyze_feedback_validation import METHODS, analyze


def fixture(tmp_path, rows):
    (tmp_path/'validation_protocol.json').write_text(json.dumps({'seeds': [600]}))
    for condition in ('medium', 'hard'):
        directory = tmp_path/condition
        directory.mkdir()
        (directory/'episodes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))


def row(method, success=True, tokens=10):
    return dict(seed=600, method=method, success=success, error=None,
        model_audit=[dict(phase='initial', actual_model_calls=1, total_tokens=999),
                     dict(phase='execution_repair', actual_model_calls=1,
                          total_tokens=tokens, accepted=True)])


def test_all_comparisons_exclude_initial_and_keep_failures(tmp_path):
    fixture(tmp_path, [row(METHODS[0]), row(METHODS[1], False, 30), row(METHODS[2], tokens=20)])
    result = analyze(tmp_path)
    assert result['complete']
    condition = result['conditions']['hard']
    assert condition['methods'][METHODS[2]]['recovery_cost']['tokens'] == 20
    comparison = condition['comparisons']['compact_vs_'+METHODS[1]]
    assert comparison['compact_only_success'] == 1
    assert comparison['per_seed_cost_deltas'][0]['tokens'] == -10
    assert condition['comparisons']['compact_vs_'+METHODS[0]]['per_seed_cost_deltas'][0]['tokens'] == 10


def test_missing_records_never_become_complete(tmp_path):
    fixture(tmp_path, [row(METHODS[0])])
    result = analyze(tmp_path)
    assert not result['complete']
    assert len(result['conditions']['medium']['missing']) == 2


def test_duplicate_records_are_rejected(tmp_path):
    fixture(tmp_path, [row(METHODS[0]), row(METHODS[0])])
    with pytest.raises(ValueError, match='duplicate'):
        analyze(tmp_path)
