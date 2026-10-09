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


def test_no_recovery_does_not_repair_success_receipt_state_disagreement():
    session = replace(build_session(), NoRecoverySupervisor)
    sup = session.supervisor
    sup.observe(session.observer.read())
    assert sup.prepare() == 'ready'
    # Use the mock's second (successful) pick, then simulate observed loss.
    session.backend.count = 1
    assert sup.execute_next(session.backend).status == 'success'
    session.backend.state.holding.clear()
    session.backend.state.at['red_cube_0'] = 'table'
    sup.observe(session.observer.read())
    assert sup.prepare() == 'safe_stop'
    assert sup.model_calls == 1


def slipped_session(cls):
    session = replace(build_session(), cls)
    sup = session.supervisor
    sup.observe(session.observer.read())
    assert sup.prepare() == 'ready'
    session.backend.count = 1
    assert sup.execute_next(session.backend).status == 'success'
    session.backend.state.holding.clear()
    session.backend.state.at['red_cube_0'] = 'table'
    sup.observe(session.observer.read())
    return session


def test_conflict_reports_observed_loss_not_prescribed_repair():
    from ch3.supervision.core import Supervisor
    session = slipped_session(Supervisor)
    request = session.supervisor._request()
    conflict = request['state_conflicts'][0]
    assert conflict['current_holding'] is None
    assert conflict['invalidated_precondition'] == 'holding(left, red_cube_0)'
    assert conflict['blocked_action']['skill'] == 'place'
    assert 'repair_actions' not in conflict
    assert 'cause' in conflict
    assert request['state_authority']


def test_direct_gets_identical_state_authority_and_raw_success_not_conflict():
    session = slipped_session(DirectReplanSupervisor)
    request = session.supervisor._request()
    assert request['held_objects']['left'] is None
    assert request['last_execution_feedback']['receipt']['status'] == 'success'
    assert request['state_authority']
    assert 'state_conflicts' not in request


def test_unknown_occupancy_does_not_report_holding_loss():
    from ch3.supervision.core import Supervisor
    session = slipped_session(Supervisor)
    session.supervisor._observation.occupancy.pop('left')
    assert session.supervisor._state_conflicts() == []


def test_confirmed_holding_does_not_report_conflict():
    from ch3.supervision.core import Supervisor
    session = replace(build_session(), Supervisor)
    sup = session.supervisor
    sup.observe(session.observer.read())
    sup.prepare()
    session.backend.count = 1
    sup.execute_next(session.backend)
    sup.observe(session.observer.read())
    assert sup._state_conflicts() == []
