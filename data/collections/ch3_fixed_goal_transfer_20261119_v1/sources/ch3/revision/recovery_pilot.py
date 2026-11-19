"""Development-only matched-information recovery/revision comparison.

B0 generates a full contract, decision and suffix. B1 translates the full
contract and uses the existing bounded solver. Ours translates a patch, reuses
a valid suffix or uses that SAME solver. No unique search algorithm is claimed.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ch3.revision.cases import make_cases
from ch3.revision.evaluation import valid_suffix
from ch3.revision.history import (Clause, Contract, Decision, Event, Obligation,
    Replacement, Snapshot, State, apply_replacement, decide)
from ch3.revision.model import build_prompt, parse_output
from ch3.revision.runtime import routed_decision
from ch3.vlm.task_semantics import decode_semantic_json

METHODS = ("direct", "full_solver", "patch_routed")
HORIZON = 8


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["pick", "place"]
    object: Literal["blue", "yellow"]
    target: Literal["table", "green", "return"] | None = None


class DirectOutput(Replacement):
    status: Literal["ready", "conflict", "need_observation"]
    actions: list[Action] = Field(max_length=HORIZON)


def make_pilot_cases():
    cases = []
    contract = Contract(0, (Obligation(Clause(id="ga", kind="at", object="blue", target="green")),
                            Obligation(Clause(id="empty", kind="empty"))))
    for name, state, history, goals, suffix in (
        ("normal", State("hand", holding="blue"), (Event(1, "pick", "blue", None),),
         contract, (Event(2, "place", "blue", "green"),)),
        ("effect_lost", State(), (Event(1, "pick", "blue", None),),
         contract, (Event(2, "place", "blue", "green"),)),
        ("blocker", State("hand", "green", "blue"), (Event(1, "pick", "blue", None),),
         Contract(0, (*contract.obligations, Obligation(Clause(id="gb", kind="at", object="yellow", target="return")))),
         (Event(2, "place", "blue", "green"), Event(3, "pick", "yellow", None), Event(4, "place", "yellow", "return"))),
        ("already_done", State("green"), (Event(1, "pick", "blue", None), Event(2, "place", "blue", "green")),
         contract, ()),
    ):
        obs = "obs-"+sha256(("recovery-dev/"+name).encode()).hexdigest()[:16]
        snapshot = Snapshot(goals, state, history, obs).validate()
        cases.append({"id": "recovery-dev-"+name, "family": name, "split": "development",
            "experiment": "E1", "initial_instruction": "INITIAL_TASK 是完整已接受任务。",
            "update": "要求不变，依据当前观测完成原任务。", "snapshot": snapshot,
            "gold": Contract(1, goals.obligations), "gold_status": "ready", "old_suffix": suffix})
    selected = {"retarget", "cancel_dependency", "past_order", "uncertain_order", "restart",
                "held_prohibition", "protect_completed", "repeat_one"}
    for source in make_cases("dev"):
        if source["family"] in selected:
            s = source["snapshot"]
            cases.append(dict(source, split="development_reused_language_cases", experiment="E2",
                              old_suffix=decide(s, s.contract, HORIZON).plan))
    return cases


def prompt(case, method, feedback=None):
    if method not in METHODS:
        raise ValueError("unknown method")
    system, user = build_prompt(case, "delta" if method == "patch_routed" else "full")
    payload = json.loads(user)
    # These factual fields are identical across all arms. No family/status/gold.
    payload["OLD_PREDICTED_SUFFIX_NOT_EXECUTED"] = [
        {"kind": e.kind, "object": e.object, "target": e.target} for e in case["old_suffix"]]
    payload["RECOVERY_ACTION_LIMIT"] = HORIZON
    payload["MODEL_REQUEST_LIMIT"] = 2
    payload["FEEDBACK"] = feedback
    if method == "direct":
        system = system.replace("Do not output plans or predict feasibility: a shared checker handles that after translation.",
            "Also decide feasibility and generate a complete remaining action suffix yourself.")
        payload["OUTPUT_SCHEMA"] = DirectOutput.model_json_schema()
        payload["OUTPUT_INSTRUCTION"] = (
            "Return COMPLETE revised clauses, status and actions. Same contract anchoring rules apply. "
            "ready requires a legal suffix satisfying all requirements under EVERY possible history. "
            "An empty ready suffix is valid if already done. conflict means no history alternative has a "
            "legal suffix within RECOVERY_ACTION_LIMIT. need_observation means no common suffix but at least "
            "one history alternative is feasible. For either non-ready status return actions: []. "
            "Do not silently weaken an impossible task. pick has target null; place needs a target. "
            "Replaying stale suffixes without current prerequisites is invalid. You may copy/reuse valid old actions.")
    return system, json.dumps(payload, ensure_ascii=False)


def candidate(case, method, content):
    """No gold access; the direct arm is checked, never repaired by the solver."""
    s = case["snapshot"]
    if method == "direct":
        raw = DirectOutput.model_validate(decode_semantic_json(content))
        proposed = apply_replacement(s, Replacement(version=raw.version, snapshot=raw.snapshot, clauses=raw.clauses))
        plan = tuple(Event(s.cursor+i+1, a.kind, a.object, a.target,
                           observation_id="prediction-only") for i, a in enumerate(raw.actions))
        decision = Decision(raw.status, plan, 0, 0, HORIZON)
        if raw.status == "ready":
            accepted = valid_suffix(s, proposed, plan)
            error = None if accepted else "INVALID_SUFFIX_OR_HISTORY_CONSTRAINT"
        else:
            # Verify the submitted claim but never replace its answer/plan.
            accepted = not plan and decide(s, proposed, HORIZON).status == raw.status
            error = None if accepted else "INCONSISTENT_BOUNDED_DECISION"
        return proposed, decision, "DIRECT_PROPOSAL", error
    proposed = parse_output(case, "delta" if method == "patch_routed" else "full", content)
    if method == "patch_routed":
        decision, route = routed_decision(s, proposed, case["old_suffix"], HORIZON)
    else:
        decision, route = decide(s, proposed, HORIZON), "SHARED_BOUNDED_SOLVER"
    return proposed, decision, route, None
