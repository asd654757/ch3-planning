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
