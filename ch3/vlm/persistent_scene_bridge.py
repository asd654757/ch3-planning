"""Restricted language goal and observed-state continuation, not open-world.

Candidates come from the known-color adapter; no simulator state is accepted.
Raw model output is persisted before parsing so rejected calls remain auditable.
"""
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ch3.execution.observed_continuation import fixed_registry
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.compiler.executable_plan import compile_plan


class GoalSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str
    target_id: str
    needs_observation: bool
    unsupported_constraints: list[str]


def logged_call(client, *, prompt, image_path, log_path):
    response = client.complete(system_prompt="Return only the requested JSON. Never invent unobserved state.",
        user_prompt=prompt, image_path=image_path, seed=0, temperature=.1, max_tokens=1024, json_mode=True)
    Path(log_path).write_text(json.dumps({"prompt": prompt, "raw_response": response.content,
        "model": response.model, "total_tokens": response.total_tokens,
        "latency_ms": response.latency_ms,
        "image_sha256": hashlib.sha256(Path(image_path).read_bytes()).hexdigest()}, indent=2), encoding="utf-8")
    return response.content


def select_goal(client, *, instruction, image_path, log_path):
    prompt = json.dumps({"instruction": instruction,
        "candidate_adapter": "restricted known-color RGB locator",
        "candidates": {"blue_candidate": "blue cube", "green_region": "green placement region"},
        "request": "Resolve instruction references using the image. Do not substitute an unsupported object. Only a single transport goal is supported. Report any prohibitions, ordering or other constraints in unsupported_constraints and set needs_observation=true; never silently discard them. If ambiguous or unsupported, set needs_observation=true. No hand-state inference requested.",
        "schema": GoalSelection.model_json_schema()}, ensure_ascii=False)
    selection = GoalSelection.model_validate_json(logged_call(client, prompt=prompt, image_path=image_path, log_path=log_path))
    if selection.needs_observation or selection.unsupported_constraints or (selection.object_id, selection.target_id) != ("blue_candidate", "green_region"):
        raise ValueError("unsupported or uncertain language goal; execution refused")
    return selection


def generate_remaining(client, *, evidence, goal, image_path, log_path):
    if evidence.get("status") != "holding_supported":
        raise ValueError("cannot plan with unknown hand state")
    if (goal.object_id, goal.target_id) != ("blue_candidate", "green_region"):
        raise ValueError("unsupported goal")
    prompt = json.dumps({"goal": f"on({goal.object_id}, {goal.target_id})",
        "observed_facts": ["holding(right, blue_candidate)"],
        "fixture_assumptions": ["on(green_region, table)"],
        "available_skills": ["pick", "place"], "available_arms": ["right"],
        "objects": ["blue_candidate", "green_region"],
        "request": "Generate ONLY the remaining plan from this current observed state. The pick already happened; never execute it again. Local step IDs start at 1. Return {actions:[{step_id,skill,object_id,target_id,arm}]}."})
    raw = json.loads(logged_call(client, prompt=prompt, image_path=image_path, log_path=log_path))
    if set(raw) != {"actions"} or not isinstance(raw["actions"], list):
        raise ValueError("unexpected plan fields")
    for action in raw["actions"]:
        if not isinstance(action, dict) or set(action) - {"step_id", "skill", "object_id", "target_id", "arm"}:
            raise ValueError("unexpected action fields")
    plan = ModelPlan.model_validate(raw)
    state = WorldState(objects={"blue_candidate", "green_region"}, holding={"right": "blue_candidate"}, at={"green_region": "table"})
    registry = fixed_registry()
    result = Validator(state.objects, registry).validate(plan, state)
    if not result.valid or f"on({goal.object_id}, {goal.target_id})" not in result.final_state.facts():
        raise ValueError("generated remainder failed Validator or predicted goal check")
    # This smoke runner can execute one remaining place, not arbitrary plans.
    if len(plan.actions) != 1 or plan.actions[0].skill.value != "place":
        raise ValueError("remainder outside smoke execution scope")
    step = compile_plan(plan, registry).steps[0]
    step.step_id = 2
    return step
