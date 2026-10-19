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


def test_compact_initial_is_shared_and_old_target_is_not_repeated():
    plain = FlexibleFixturePlanner(None, recovery=True, shared={},
        execution_contract=True, execution_diagnostics=True)
    compact = FlexibleFixturePlanner(None, recovery=True, shared={},
        execution_contract=True, execution_diagnostics=True, compact_feedback=True)
    assert plain.planning_snapshot() == compact.planning_snapshot()
    event = dict(error_code='OBSERVED_GOAL_NOT_SATISFIED',
        attempted_action={'target_id': 'obsolete_target'}, observation={})
    import json
    snapshot = compact.planning_snapshot(event)
    assert 'obsolete_target' not in json.dumps(snapshot)
    assert snapshot['objects'] == plain.planning_snapshot(event)['objects']


def test_compact_rejection_is_not_observed_state_or_action_synthesis():
    planner = FlexibleFixturePlanner(None, recovery=True, shared={},
        execution_contract=True, execution_diagnostics=True, compact_feedback=True)
    event = dict(error_code='DESTINATION_OCCUPIED', transfer_index=0,
        object_id='yellow_candidate', target_id='return_region',
        occupying_objects=['blue_candidate'], rejected_candidate={'actions': ['not executed']})
    review = planner.planning_snapshot(event)['rejected_candidate_review']
    assert review['candidate_executed'] is False
    assert 'candidate' not in review
    assert review['constraint_details']['occupying_objects'] == ['blue_candidate']
