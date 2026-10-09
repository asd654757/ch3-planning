from ch3.supervision.comparison import DirectReplanSupervisor, NoRecoverySupervisor, SharedInitialPlanner
from ch3.supervision.demo import build_session
from ch3.supervision.runtime import Session, run_session


def replace(session, cls):
    old = session.supervisor
    return Session(cls(old.task, old.validator, old.planner), session.observer,
                   session.backend, session.evidence_kind)


def test_no_recovery_stops_after_failed_command():
    result = run_session(replace(build_session(), NoRecoverySupervisor))
    assert not result['confirmed_complete']
    assert result['commands'] == result['model_calls'] == 1
    assert result['stop_reason'] == 'safe_stop'


def test_direct_replan_keeps_state_and_raw_feedback_but_not_diagnostics():
    session = replace(build_session(), DirectReplanSupervisor)
    assert run_session(session)['confirmed_complete']
    request = session.supervisor.audit[1]['request']
    assert request['last_execution_feedback']['receipt']['status'] == 'failed'
    assert request['held_objects'] == {'left': None, 'right': None}
    assert 'executed_history_not_current_state' not in request
    assert 'rejection_feedback' not in request
    assert 'goal_facts' in request


def test_shared_initial_is_replayed_only_once():
    class Delegate:
        def generate(self, request, images):
            return 'new'
    planner = SharedInitialPlanner(Delegate(), 'shared')
    assert planner.generate({}, ()) == 'shared'
    assert planner.actual_calls == 0
    assert planner.generate({}, ()) == 'new'
    assert planner.actual_calls == 1
