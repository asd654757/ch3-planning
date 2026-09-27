from ch3.capability.registry import CapabilityRegistry
from ch3.repair.observed_recovery import recover_from_observation
from ch3.schema.model_plan import GoalSpec
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


def setup_recovery():
    registry = CapabilityRegistry.from_yaml("config/capability_registry.yaml")
    validator = Validator({"target", "decoy", "tray"}, registry)
    state = WorldState.table_scene(validator.scene_objects)
    state.holding["left"] = "decoy"
    return validator, state, GoalSpec(facts=["on(target, tray)"])


def test_release_and_continue_from_observed_state():
    validator, state, goal = setup_recovery()
    outcome = recover_from_observation(
        observed_state=state, goal=goal, validator=validator,
        allowed_skills={"pick", "place"},
    )
    assert outcome.accepted
    assert [action.skill.value for action in outcome.actions] == ["place", "pick", "place"]
    assert outcome.actions[0].object_id == "decoy"
    assert outcome.actions[0].target_id == "table"
    assert outcome.final_state.at["target"] == "tray"
    assert state.holding == {"left": "decoy"}


def test_feedback_mismatch_stops_before_next_action():
    validator, state, goal = setup_recovery()
    calls = []

    def failed_release(before, action):
        calls.append(action)
        return before

    outcome = recover_from_observation(
        observed_state=state, goal=goal, validator=validator,
        allowed_skills={"pick", "place"}, observe_after_action=failed_release,
    )
    assert not outcome.accepted
    assert outcome.reason == "observation_mismatch"
    assert len(calls) == len(outcome.actions) == 1


def test_no_registered_safe_release_target():
    validator, state, goal = setup_recovery()
    validator.special_targets = set()
    outcome = recover_from_observation(
        observed_state=state, goal=goal, validator=validator,
        allowed_skills={"pick", "place"},
    )
    assert not outcome.accepted
    assert outcome.reason == "no_safe_release_target"


def test_goal_already_observed_needs_no_replay():
    validator, state, goal = setup_recovery()
    state.at["target"] = "tray"
    outcome = recover_from_observation(
        observed_state=state, goal=goal, validator=validator,
        allowed_skills={"pick", "place"},
    )
    assert outcome.accepted
    assert outcome.actions == []


def test_online_claim_requires_independent_observer():
    validator, state, goal = setup_recovery()
    outcome = recover_from_observation(
        observed_state=state, goal=goal, validator=validator,
        allowed_skills={"pick", "place"}, symbolic_only=False,
    )
    assert not outcome.accepted
    assert outcome.reason == "online_observation_required"
