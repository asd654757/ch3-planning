"""Restricted language-update contracts. Gold annotations never enter model prompts.

This is a symbolic semantic pilot, not perception or physical authorization.
"""
import json
from pydantic import BaseModel, ConfigDict
from typing import Literal
from ch3.schema.model_plan import ModelPlan
from ch3.vlm.task_switch_recovery import switch_fixture
from ch3.execution.observed_continuation import fixed_registry
from ch3.validator.pipeline import Validator
from ch3.vlm.persistent_scene_bridge import logged_call

CANDIDATES = {"blue_candidate": "blue cube", "yellow_candidate": "yellow cube", "green_region": "green placement region"}
MOVABLE = {"blue_candidate", "yellow_candidate"}
TARGETS = {"table", "green_region"}

class LocationGoal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    object_id: str
    target_id: str

class TaskSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ready", "clarify", "unsupported"]
    goals: list[LocationGoal]
    forbidden_objects: list[str]
    placement_order: list[str]
    hand_empty: bool

    def check_scope(self):
        if self.status != "ready":
            if self.goals or self.forbidden_objects or self.placement_order or self.hand_empty:
                raise ValueError("refusal must not authorize partial goals")
            return
        ids = [g.object_id for g in self.goals]
        if len(ids) != len(set(ids)) or any(g.object_id not in MOVABLE or g.target_id not in TARGETS for g in self.goals):
            raise ValueError("unknown or conflicting goal")
        if (len(self.forbidden_objects) != len(set(self.forbidden_objects)) or
            not set(self.forbidden_objects) <= MOVABLE or
            len(self.placement_order) != len(set(self.placement_order)) or
            not set(self.placement_order) <= set(ids)):
            raise ValueError("unknown/duplicate constraint")
        if not self.goals and not self.hand_empty:
            raise ValueError("ready contract needs goal")


class SemanticEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str
    quote: str


class GroundedSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract: TaskSemantics
    evidence: list[SemanticEvidence]

    def check_evidence(self, instruction):
        """Check provenance coverage only, NOT that a quote entails its claim."""
        self.contract.check_scope()
        fields = [e.field for e in self.evidence]
        if len(fields) != len(set(fields)):
            raise ValueError("duplicate evidence field")
        required = {"status"}
        required |= {f"goals.{i}" for i in range(len(self.contract.goals))}
        required |= {f"forbidden_objects.{i}" for i in range(len(self.contract.forbidden_objects))}
        if self.contract.placement_order:
            required.add("placement_order")
        if self.contract.hand_empty:
            required.add("hand_empty")
        if set(fields) != required:
            raise ValueError("missing or unexpected semantic evidence fields")
        for e in self.evidence:
            if not e.quote.strip() or e.quote not in instruction:
                raise ValueError("semantic evidence is not an exact instruction substring")


def parse_semantics(client, *, instruction, image_path, log_path, version="v2"):
    # No expected goals, answer plan, case family or annotation is supplied.
    if version == "v1":
        raise ValueError("legacy v1 outputs retained; use its frozen source to reproduce")
    if version != "v2":
        raise ValueError("unknown semantic parser version")
    prompt = json.dumps({
        "NEW_USER_INSTRUCTION": instruction,
        "candidate_catalog": CANDIDATES,
        "available_destinations": {"table": "桌面 / table", "green_region": "绿色区域 / green region"},
        "SUPPORTED_CURRENT_STATE": {"held_by_right": "blue_candidate",
            "yellow_candidate_location": "table"},
        "state_source": "explicit_fixture_not_visual_state_estimation",
        "historical_context": "A blue pick has already executed. The old blue-to-green destination is HISTORY ONLY, never a default goal. Parse the new instruction independently.",
        "output": {
            "contract": {"status": "ready OR clarify OR unsupported", "goals": "array of {object_id,target_id}",
                         "forbidden_objects": "array of candidate IDs", "placement_order": "array of candidate IDs",
                         "hand_empty": "boolean"},
            "evidence": "array of {field,quote}; quote is an EXACT substring of NEW_USER_INSTRUCTION"},
        "rules": [
            "READ THE NEW INSTRUCTION, not the old destination or what looks convenient in the image. Use the instruction's explicit destinations.",
            "goals includes ALL required final positions, including return/put back/放回/放回桌面. Returning to table requires an object→table goal; hand_empty alone does NOT represent it.",
            "held-object references resolve to blue_candidate using the supplied current state. A vague 'that cube over there' with no definite object AND destination requires clarify, never reuse the historical goal.",
            "Any requested unavailable object (even after a valid clause), unknown destination or unsupported skill makes the WHOLE request unsupported. Never substitute another object or authorize the valid part.",
            "forbidden_objects prohibits future pick/place, including move-and-return. Preserve every do-not-touch/do-not-move restriction.",
            "placement_order is EMPTY unless the instruction explicitly orders GOAL-ACHIEVING placements of at least two objects. Ordering temporary release before picking is not an order between final goal placements. Never insert singleton order.",
            "hand_empty=true for explicit empty-handed/空手 AND instructions to put down, return, place or complete transportation. false only if no such requirement exists.",
            "ready only when references and destinations are grounded. clarify/unsupported must have empty goals, forbidden_objects, placement_order and hand_empty=false.",
            "Evidence fields: status always; goals.0, goals.1 etc for each goal; forbidden_objects.0 etc for each prohibition; placement_order if nonempty; hand_empty if true. No other fields. Goal quote must include its destination or the applicable shared destination clause. Reuse quotes when justified.",
            "Do not add unrequested goals, movement order or destinations. No schema, markdown, commentary; emit populated contract and evidence records."
        ]}, ensure_ascii=False)
    grounded = GroundedSemantics.model_validate_json(logged_call(client, prompt=prompt,
        image_path=image_path, log_path=log_path))
    grounded.check_evidence(instruction)
    from pathlib import Path
    Path(log_path).with_name("semantic_evidence_audit.json").write_text(json.dumps({
        "version": version, "quote_coverage_valid": True,
        "semantic_correctness_guaranteed": False,
        "grounded_output": grounded.model_dump()}, ensure_ascii=False, indent=2))
    return grounded.contract


def semantic_equal(a, b):
    def canonical(s):
        return (s.status, sorted((g.object_id, g.target_id) for g in s.goals),
                sorted(s.forbidden_objects), s.placement_order, s.hand_empty)
    return canonical(a) == canonical(b)


def evaluate_plan(raw, contract):
    contract.check_scope()
    if contract.status != "ready":
        return {"accepted": False, "reason": "semantic_refusal"}
    if not isinstance(raw, dict) or set(raw) != {"actions"} or not isinstance(raw["actions"], list):
        raise ValueError("invalid plan envelope")
    state = switch_fixture()
    if raw["actions"]:
        allowed = {"step_id", "skill", "object_id", "target_id", "arm"}
        if any(not isinstance(a, dict) or set(a)-allowed for a in raw["actions"]):
            raise ValueError("extra action fields")
        plan = ModelPlan.model_validate(raw)
        if [a.step_id for a in plan.actions] != list(range(1, len(plan.actions)+1)):
            raise ValueError("noncontiguous IDs")
        result = Validator(state.objects, fixed_registry()).validate(plan, state)
        valid, final = result.valid, result.final_state
        actions = raw["actions"]
    else:
        valid, final, actions = True, state, []  # genuine already-satisfied no-op
    goal_ok = bool(valid and final and all(final.location_of(g.object_id) == g.target_id for g in contract.goals)
                   and (not contract.hand_empty or final.arm_empty("right")))
    forbidden_ok = all(a["object_id"] not in contract.forbidden_objects for a in actions)
    goal_targets = {g.object_id: g.target_id for g in contract.goals}
    positions = {o: next((i for i,a in enumerate(actions) if a["skill"] == "place" and
                 a["object_id"] == o and a.get("target_id") == goal_targets[o]), None)
                 for o in contract.placement_order}
    ordered = [positions[o] for o in contract.placement_order]
    order_ok = not ordered or (all(i is not None for i in ordered) and ordered == sorted(ordered))
    return {"valid": bool(valid), "goal_satisfied": goal_ok, "forbidden_ok": forbidden_ok,
            "order_ok": order_ok, "accepted": bool(goal_ok and forbidden_ok and order_ok),
            "physical_execution_authorized": False}


def deterministic_plan(contract):
    """Generic bounded symbolic search, not a per-case handwritten answer."""
    from collections import deque
    contract.check_scope()
    if contract.status != "ready":
        return None
    queue = deque([[]])
    # Two cubes and two targets, max six steps; exhaustive, same registered skills.
    while queue:
        actions = queue.popleft()
        raw = {"actions": actions}
        audit = evaluate_plan(raw, contract)
        if audit["accepted"]:
            return raw
        if len(actions) >= 6 or not audit["forbidden_ok"]:
            continue
        # Complete-plan Validator rejects a terminal held state. Search prefixes
        # may legitimately end in pick; prune only actual transition failures.
        if actions:
            from ch3.validator.state_validator import simulate_plan
            ok, _, _, _, _ = simulate_plan(ModelPlan.model_validate(raw), switch_fixture(),
                                           valid_targets=set(CANDIDATES) | {"table"})
            if not ok:
                continue
        for obj in sorted(MOVABLE - set(contract.forbidden_objects)):
            for skill, target in [("pick", None), ("place", "table"), ("place", "green_region")]:
                a = dict(step_id=len(actions)+1, skill=skill, object_id=obj, arm="right")
                if target: a["target_id"] = target
                queue.append(actions+[a])
    return None


def generate_plan(client, *, contract, image_path, log_path):
    contract.check_scope()
    if contract.status != "ready":
        raise ValueError("refused semantics")
    prompt = json.dumps({"task_contract": contract.model_dump(), "current_facts": sorted(switch_fixture().facts()),
        "state_source": "explicit_fixture_not_perception", "candidates": CANDIDATES,
        "available_skills": ["pick", "place"], "arms": ["right"], "special_targets": ["table"],
        "planning_notes": "A held blue object must be released BEFORE any yellow pick. If yellow must reach green before blue, temporarily place blue on table, then pick/place yellow green, then pick/place blue green. Temporary releases must not violate prohibitions. This is a generic current-state dependency, not a replacement for the task contract.",
        "rules": "Return ONLY {actions:[...]}. Each action has contiguous step_id starting 1, skill, object_id, arm, and target_id for place. Generate ONLY remaining actions: blue pick is already executed, blue is held. Never replay history. Each arm holds at most one object. Honor final goals AND every prohibition/order constraint. Empty actions allowed only if already satisfied. Do not invent constraints or objects."})
    return json.loads(logged_call(client, prompt=prompt, image_path=image_path, log_path=log_path))
