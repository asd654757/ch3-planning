from scripts.run_task_switch_benchmark import make_cases


def test_frozen_cases_balanced_matched_and_no_duplicate_ids():
    cases = make_cases(10)
    assert len(cases) == 50
    assert len({c['case_id'] for c in cases}) == 50
    for seed in range(10):
        rows = [c for c in cases if c['seed'] == seed]
        assert len(rows) == 5
        assert sum(c['blackout_destination'] for c in rows) == 2
        assert [c['reobserve_budget'] for c in rows if not c['blackout_destination']].count(0) == 1
    assert cases[0]['variant'] != cases[5]['variant']


def test_scale_gate_rejects_missing_feedback_execution(tmp_path, monkeypatch):
    import json
    from scripts.task_switch_scale_gate import audit
    row = dict(case_id='x', exit_code=0, episode_resets=1, recovery_episode_resets=0,
               blackout_destination=True, yellow_placement_attempted=True, reobserve_budget=2)
    (tmp_path / 'summary.json').write_text(json.dumps({'records': [row]}))
    (tmp_path / 'manifest.json').write_text(json.dumps({'cases': [row], 'code_sha256': {}}))
    (tmp_path / 'x').mkdir()
    (tmp_path / 'x' / 'summary.json').write_text(json.dumps({'events': [{'stage':'blue_pick'}]}))
    result = audit(tmp_path)
    assert not result['ready_for_limited_task_switch_formal']
    assert any('missing_feedback' in error for error in result['errors'])
    row['yellow_placement_attempted'] = False
    (tmp_path / 'summary.json').write_text(json.dumps({'records': [row]}))
    assert audit(tmp_path)['ready_for_limited_task_switch_formal']
