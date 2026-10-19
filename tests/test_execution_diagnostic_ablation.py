from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner


def test_diagnostic_ablation_keeps_state_task_and_rules_identical():
    event = dict(error_code='OBSERVED_GOAL_NOT_SATISFIED',
                 attempted_action={'skill': 'place', 'object_id': 'blue_candidate',
                                   'target_id': 'return_region'},
                 observation={'goal_status': 'not_satisfied', 'location': 'observed_support'})
    requests = []
    for enabled in (False, True):
        planner = FlexibleFixturePlanner(None, recovery=True, shared={},
            error_feedback=enabled, execution_contract=True, execution_diagnostics=True)
        requests.append(planner.planning_snapshot(event))
    direct, full = requests
    assert direct.pop('execution_diagnostic') is None
    evidence = full.pop('execution_diagnostic')
    assert evidence['action_executed'] is True
    assert evidence['event_type'] == 'execution_event'
    assert full['rejected_candidate_review'] is None
    assert direct == full


def test_legacy_snapshot_remains_unchanged():
    planner = FlexibleFixturePlanner(None, recovery=True, shared={}, execution_contract=True)
    assert 'execution_diagnostic' not in planner.planning_snapshot()
