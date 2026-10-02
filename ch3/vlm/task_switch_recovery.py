"""Bounded task-switch planning diagnostic; explicit state, NOT perception.

Task update is an external command, not an unknown-object fault. The originally
held blue object must be returned to table before transporting yellow. This
module validates/compiles plans but does not authorize physical execution.
"""
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from ch3.execution.recovery_context import RecoveryContext
from ch3.compiler.executable_plan import compile_plan
from ch3.execution.observed_continuation import fixed_registry
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.persistent_scene_bridge import logged_call


def switch_fixture():
    return WorldState(objects={"blue_candidate", "yellow_candidate", "green_region"},
                      holding={"right": "blue_candidate"},
                      at={"yellow_candidate": "table", "green_region": "table"})


def evaluate_switch_plan(raw, state, *, state_source="explicit_fixture_not_visual_state_estimation"):
    """All facts supplied explicitly; no inferred empty-hand or object poses."""
    expected = switch_fixture()
    if state != expected:
        raise ValueError("state outside explicit task-switch fixture")
    if not isinstance(raw, dict) or set(raw) != {"actions"} or not isinstance(raw["actions"], list):
        raise ValueError("unexpected top-level plan fields")
    allowed = {"step_id", "skill", "object_id", "target_id", "arm"}
    if any(not isinstance(a, dict) or set(a) - allowed for a in raw["actions"]):
        raise ValueError("unexpected action fields")
    plan = ModelPlan.model_validate(raw)
    registry = fixed_registry()
    validation = Validator(state.objects, registry).validate(plan, state)
    goals = {"on(yellow_candidate, green_region)", "on(blue_candidate, table)"}
    goal_ok = bool(validation.valid and validation.final_state and
                   goals <= validation.final_state.facts() and validation.final_state.arm_empty("right"))
    # Limit this diagnostic to the three-action remaining sequence, not arbitrary
    # expansion or modifications to the completed historical pick.
    in_scope = len(plan.actions) == 3 and [a.skill.value for a in plan.actions] == ["place", "pick", "place"]
    accepted = bool(goal_ok and in_scope)
    audit = {"plan": plan.model_dump(mode="json"), "valid": validation.valid,
             "error_code": str(validation.error_code), "goal_satisfied": goal_ok,
             "within_execution_scope": in_scope, "accepted": accepted,
             "current_facts": sorted(state.facts()), "required_final_facts": sorted(goals),
             "state_source": state_source,
             "physical_execution_authorized": False}
    return audit, compile_plan(plan, registry) if accepted else None


def generate_switch_plan(client, *, image_path, log_path, state, context=None):
    # Validate the state before calling the model.
    if state != switch_fixture():
        raise ValueError("state outside explicit task-switch fixture")
    instruction = "Cancel the unexecuted blue-to-green placement. Now transport the yellow cube to the green region, returning the held blue cube to table."
    goals = {"on(yellow_candidate, green_region)", "on(blue_candidate, table)", "hand_empty(right)"}
    facts = state.facts() | state.empty_hand_facts({"right"})
    image_digest = sha256(Path(image_path).read_bytes()).hexdigest()
    if context is None:
        context = RecoveryContext(instruction, goals)
        context.observe(facts=facts, image_sha256=image_digest,
                        evidence_source="explicit_fixture_not_visual_state_estimation")
    if context.instruction != instruction or context.goals != goals or context.current_facts != facts:
        raise ValueError("task-switch context does not match current fixture")
    # Bind pixels as well as facts/source to the request. Never let a caller
    # silently pair a new image with an old observation version.
    from ch3.execution.recovery_context import digest
    expected_digest = digest({"facts": sorted(facts), "image_sha256": image_digest,
                             "source": context.evidence_source, "status": "supported"})
    if context.observation_digest != expected_digest:
        raise ValueError("image does not match bound observation")
    ticket = context.issue_request()
    binding_path = Path(log_path).with_name("switch_request_binding.json")
    binding = {"ticket": asdict(ticket), "response_current": False,
               "physical_execution_authorized": False}
    binding_path.write_text(json.dumps(binding, indent=2))
    prompt = json.dumps({"task_update": instruction,
        "request_context": asdict(ticket),
        "remaining_goal_facts": sorted(context.remaining_goals),
        "state_source": context.evidence_source,
        "current_facts": sorted(state.facts()),
        "objects": {"blue_candidate": "blue cube", "yellow_candidate": "yellow cube", "green_region": "green region"},
        "required_final_facts": ["on(yellow_candidate, green_region)", "on(blue_candidate, table)", "hand_empty(right)"],
        "available_skills": ["pick", "place"], "available_arms": ["right"], "special_targets": ["table"],
        "request": "Generate only the remaining plan from the current state. Do not replay the completed historical pick. Each arm holds at most one object. Different skills use the SAME candidate IDs, never descriptive labels. Return one JSON object with the single key actions: an array of populated records containing step_id, skill, object_id, target_id (place only), arm. Local IDs start at 1. No schema, markdown or commentary."})
    raw = json.loads(logged_call(client, prompt=prompt, image_path=image_path, log_path=log_path))
    try:
        context.check_request(ticket)
    except ValueError as exc:
        binding["rejection"] = str(exc)
        binding_path.write_text(json.dumps(binding, indent=2))
        raise
    audit, executable = evaluate_switch_plan(raw, state, state_source=context.evidence_source)
    audit["request_context"] = asdict(ticket)
    binding["response_current"] = True
    binding_path.write_text(json.dumps(binding, indent=2))
    context.consume_request(ticket)
    Path(log_path).with_name("switch_plan_validation.json").write_text(json.dumps(audit, indent=2))
    if executable is None:
        raise ValueError("task-switch plan rejected")
    return executable
