from dataclasses import replace
import pytest
from ch3.execution.recovery_context import RecoveryContext, recovery_route


def context():
    c = RecoveryContext("transport blue", {"on(blue, green)"})
    c.observe(facts={"on(blue, table)"}, image_sha256="image-a", evidence_source="test_fixture")
    return c


def test_task_switch_invalidates_old_response_preserves_history():
    c = context()
    c.record_execution(event_id="pick-1", skill="pick", object_id="blue", outcome="supported_success", evidence_source="test")
    ticket = c.issue_request()
    c.update_task("transport yellow", {"on(yellow, green)"})
    with pytest.raises(ValueError, match="stale"):
        c.check_request(ticket)
    assert len(c.history) == 1 and c.remaining_goals == {"on(yellow, green)"}


def test_new_observation_including_same_image_invalidates_response():
    c = context()
    t = c.issue_request()
    c.observe(facts=c.current_facts, image_sha256="image-a", evidence_source="test_fixture")
    with pytest.raises(ValueError, match="stale"):
        c.check_request(t)


def test_goal_effect_can_be_lost_without_rewriting_history():
    c = context()
    c.record_execution(event_id="place-1", skill="place", object_id="blue", outcome="supported_success", evidence_source="test")
    assert c.remaining_goals  # Commands/history are not observed effects.
    c.observe(facts={"on(blue, green)"}, image_sha256="b", evidence_source="test")
    assert not c.remaining_goals
    c.observe(facts={"on(blue, table)"}, image_sha256="c", evidence_source="test")
    assert c.remaining_goals and len(c.history) == 1


def test_unknown_does_not_retain_prior_facts_or_issue_request():
    c = context()
    c.observe(facts=[], image_sha256="black", evidence_source="test", status="unknown")
    assert not c.current_facts
    with pytest.raises(ValueError):
        c.issue_request()


def test_request_is_local_bound_and_single_use():
    c = context()
    t = c.issue_request()
    with pytest.raises(ValueError):
        c.check_request(replace(t, task_version=99))
    c.consume_request(t)
    with pytest.raises(ValueError):
        c.consume_request(t)


def test_duplicate_event_and_unknown_facts_rejected():
    c = context()
    kw = dict(event_id="x", skill="pick", object_id="blue", outcome="unknown", evidence_source="test")
    c.record_execution(**kw)
    with pytest.raises(ValueError):
        c.record_execution(**kw)
    with pytest.raises(ValueError):
        c.observe(facts={"hand_empty(right)"}, image_sha256="black", evidence_source="test", status="unknown")


@pytest.mark.parametrize("status,prereq,intent,kwargs,expected", [
    ("supported_failure", "supported", "aligned", {"unchanged_task_and_state": True, "retry_authorized": True}, "BOUNDED_FIXED_RETRY"),
    ("supported_failure", "supported", "aligned", {"unchanged_task_and_state": True, "retry_authorized": True, "retries_used": 1}, "REPLAN_FROM_CURRENT_STATE"),
    ("supported_success", "supported", "misaligned", {}, "REPLAN_FROM_CURRENT_STATE"),
    ("supported_success", "violated", "aligned", {}, "REPLAN_FROM_CURRENT_STATE"),
    ("unknown", "supported", "aligned", {}, "REOBSERVE"),
    ("supported_success", "unknown", "aligned", {"reobservations_used": 2}, "STOP"),
    ("supported_success", "supported", "aligned", {}, "CONTINUE"),
])
def test_routing_separates_past_outcome_next_prerequisites(status, prereq, intent, kwargs, expected):
    assert recovery_route(execution_status=status, next_prerequisites=prereq, intent=intent, **kwargs) == expected
