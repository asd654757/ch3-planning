import pytest
from ch3.execution.observed_continuation import checked_place_continuation


def test_unknown_does_not_manufacture_hand_empty_or_plan():
    decision, step = checked_place_continuation({"status": "unknown"}, target_visible=True, observation_id="new")
    assert step is None
    assert decision["action"] == "observe_again"
    assert decision["observed_facts"] == []
    assert decision["validator_valid"] is None


def test_supported_holding_compiles_only_after_validation():
    decision, step = checked_place_continuation({"status": "holding_supported"}, target_visible=True, observation_id="new")
    assert decision["validator_valid"] and decision["predicted_goal_satisfied"]
    assert decision["action"] == "execute_remaining"
    assert "holding(right, blue_candidate)" in decision["observed_facts"]
    assert "on(green_region, table)" not in decision["observed_facts"]
    assert decision["fixture_assumptions"] == ["on(green_region, table)"]
    assert step.step_id == 2 and step.source_skill == "place"
    assert step.policy_id == "fixed_place"


def test_missing_destination_requires_observation():
    decision, step = checked_place_continuation({"status": "holding_supported"}, target_visible=False, observation_id="new")
    assert step is None and decision["reason"] == "destination_not_observed"


def test_observation_identity_required():
    with pytest.raises(ValueError):
        checked_place_continuation({"status": "unknown"}, target_visible=True, observation_id="")
