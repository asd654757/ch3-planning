"""Narrow observed-state gate for the right-arm pick/place smoke.

Never promote unknown hand evidence to an empty hand. Only the two relevant
entities enter this local continuation model; this is not a full scene state.
"""
from ch3.capability.registry import CapabilityRegistry
from ch3.compiler.executable_plan import compile_plan
from ch3.schema.model_plan import ModelPlan, ModelPlanAction
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


def fixed_registry():
    return CapabilityRegistry({
        "arms": ["right"],
        "capabilities": {
            "pick": {"policy": "fixed_pick", "primitive": "grasp", "args": ["object_id", "arm"]},
            "place": {"policy": "fixed_place", "primitive": "place", "args": ["object_id", "target_id", "arm"]},
        },
        "special_targets": {"table": {}},
    })


def checked_place_continuation(evidence, *, target_visible, observation_id):
    """Return decision and compiled step, never a plan for unknown evidence.

    Names and the region-on-table assumption are restricted smoke fixture
    semantics, not inferred language grounding or general scene estimation.
    """
    decision = {"action": "observe_again", "reason": "holding_not_supported",
                "observation_id": observation_id, "observed_facts": [],
                "fixture_assumptions": [],
                "validator_valid": None, "predicted_goal_satisfied": None}
    if not observation_id:
        raise ValueError("missing observation identity")
    if evidence.get("status") != "holding_supported":
        return decision, None
    if not target_visible:
        decision["reason"] = "destination_not_observed"
        return decision, None
    state = WorldState(objects={"blue_candidate", "green_region"},
                       holding={"right": "blue_candidate"}, at={"green_region": "table"})
    registry = fixed_registry()
    remaining = ModelPlan(actions=[ModelPlanAction(step_id=1, skill="place", arm="right",
        object_id="blue_candidate", target_id="green_region")])
    validation = Validator(state.objects, registry).validate(remaining, state)
    predicted = bool(validation.valid and validation.final_state and
                     "on(blue_candidate, green_region)" in validation.final_state.facts())
    decision.update(observed_facts=["holding(right, blue_candidate)"],
                    fixture_assumptions=["on(green_region, table)"], validator_valid=validation.valid,
                    predicted_goal_satisfied=predicted,
                    reason="remaining_plan_validated" if predicted else "remaining_plan_rejected",
                    action="execute_remaining" if predicted else "stop")
    if not predicted:
        return decision, None
    step = compile_plan(remaining, registry).steps[0]
    # Suffix validation uses local numbering; execution retains global order.
    step.step_id = 2
    return decision, step
