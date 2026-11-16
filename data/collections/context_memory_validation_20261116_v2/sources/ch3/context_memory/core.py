"""Five matched-backend arms and two manually abstracted, source-backed memories.

Selectors only read accepted tasks and observed inputs, never evaluation labels.
Dependency closure is deliberately shared with the strong combination baseline.
"""
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from math import sqrt
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ch3.revision.evaluation import valid_suffix
from ch3.revision.feedback import explain_rejection
from ch3.revision.history import Decision, Event, OBJECTS, decide, monitor, possible_histories
from ch3.revision.recovery_pilot import Action
from ch3.revision.runtime import RevisionRuntime
from ch3.vlm.task_semantics import decode_semantic_json

METHODS = ("full", "filtered", "full_memory", "filtered_memory", "joint")
HORIZON = 8


class PlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int
    snapshot: str
    status: Literal["ready", "conflict", "need_observation"]
    actions: list[Action] = Field(max_length=HORIZON)


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def build_memory(root):
    """Verified past candidates, with HUMAN-authored abstractions/advice.

    This does not automatically discover or causally prove general failure rules.
    Source outputs are not retrieved as exact answers for a new task.
    """
    root = Path(root)
    before_path = root/"data/collections/revision_recovery_model_20261113_v1/results.json"
    after_path = root/"data/collections/revision_recovery_direct_diagnostic_20261113_v2/results.json"
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    records = []
    for key, case_id, title, tags, advice in (
        ("occupied_destination", "recovery-dev-blocker", "Placement and destination occupancy", "at visit place target holding hand occupied",
         "A placement failed its destination-capacity precondition. Before placing, account for any occupying object and current hand occupancy. Temporary placement can be needed before moving a blocker. Preserve all goals and prohibitions; this experience does not authorize moving a protected object."),
        ("uncertain_first_event", "dev-uncertain_order-0", "Unknown history and first-visit order", "before visit history unknown first place order",
         "Unknown historical placement evidence caused an incorrect conflict/ready claim. Evaluate all evidence alternatives for first-visit ordering. Repeating an action cannot replace an earlier first event. Respect each visit's activation time; request evidence only when no common valid suffix exists and some alternatives remain feasible."),
    ):
        source = next(r for r in before if r["id"] == case_id and r["method"] == "direct")
        fixed = next(r for r in after if r["id"] == case_id and r["method"] == "direct")
        if not source["attempts"][0].get("rejection") or not fixed["accepted"]:
            raise ValueError("missing rejected and verified corrected source")
        records.append({"id": key, "title": title, "tags": tags, "advice": advice,
            "abstraction": "human_authored_from_past_verified_candidate_diagnostics",
            "provenance": [{"path": str(p.relative_to(root)), "sha256": digest(p), "case_id": case_id,
                            "method": "direct", "attempt": 1 if p == before_path else len(fixed["attempts"])}
                           for p in (before_path, after_path)]})
    return tuple(records)


def verify_memory_sources(root, memory):
    """Replay ONLY past source cases; no new benchmark labels are consulted."""
    from scripts.analyze_history_revision import load_case
    from ch3.revision.recovery_pilot import candidate
    root = Path(root)
    checked = []
    for item in memory:
        for i, source in enumerate(item["provenance"]):
            path = root/source["path"]
            if digest(path) != source["sha256"]:
                raise ValueError("memory source changed")
            cases = {c["id"]: load_case(c) for c in json.loads((path.parent/"cases.json").read_text())}
            row = next(r for r in json.loads(path.read_text()) if r["id"] == source["case_id"] and r["method"] == source["method"])
            attempt = row["attempts"][source["attempt"]-1]
            _, _, _, error = candidate(cases[source["case_id"]], "direct", attempt["content"])
            if (error is None) != (i == 1):
                raise ValueError("memory rejection/correction witness not reproduced")
            checked.append({"memory": item["id"], "source": source["path"], "replayed": True, "rejected": error is not None})
    return checked


def all_records(snapshot):
    return {**{"state:"+obj: {"id": "state:"+obj, "kind": "location", "object": obj,
                             "value": getattr(snapshot.state, obj)} for obj in OBJECTS},
        "state:holding": {"id": "state:holding", "kind": "holding", "value": snapshot.state.holding},
        **{"occupancy:"+region: {"id": "occupancy:"+region, "kind": "observed_region_occupancy",
            "target": region, "occupants": tuple(obj for obj in OBJECTS if getattr(snapshot.state, obj) == region)}
           for region in ("green", "return")},
        **{"event:"+str(e.seq): {"id": "event:"+str(e.seq), "kind": "event", **asdict(e)} for e in snapshot.history}}


def dependency_ids(snapshot):
    """Conservative task closure including blockers, hands and temporal anchors.

    All clauses and domain constraints are ALWAYS retained separately. Current
    region occupancy is included conservatively for temporary placements too.
    """
    relevant = {o.clause.object for o in snapshot.contract.obligations if o.clause.object}
    if snapshot.state.holding:
        relevant.add(snapshot.state.holding)
    if relevant:
        relevant.update(obj for obj in OBJECTS if getattr(snapshot.state, obj) in ("green", "return"))
    result = {"state:holding", *("state:"+obj for obj in relevant)}
    if relevant:
        result.update(("occupancy:green", "occupancy:return"))
    for obligation in snapshot.contract.obligations:
        c = obligation.clause
        if c.kind in ("visit", "forbid"):
            result.update("event:"+str(e.seq) for e in snapshot.history
                          if e.object == c.object and e.seq > obligation.since)
    return frozenset(result)


def memory_witness(memory, snapshot):
    """Source-pattern applicability, not task feasibility or a generated plan."""
    needed = set()
    clauses = {o.clause.id: o for o in snapshot.contract.obligations}
    if memory["id"] == "occupied_destination":
        checks = [monitor(snapshot.contract, snapshot.state, h)[1] for h in possible_histories(snapshot.history)]
        for o in snapshot.contract.obligations:
            c = o.clause
            if c.kind not in ("at", "visit") or c.target == "table":
                continue
            pending = (getattr(snapshot.state, c.object) != c.target if c.kind == "at"
                       else any(c.id in pending for pending in checks))
            if pending:
                for obj in OBJECTS:
                    if obj != c.object and getattr(snapshot.state, obj) == c.target:
                        needed.update(("state:"+obj, "state:"+c.object, "state:holding"))
    elif memory["id"] == "uncertain_first_event":
        visits = [clauses[ref] for o in snapshot.contract.obligations if o.clause.kind == "before"
                  for ref in (o.clause.first, o.clause.second)]
        for o in visits:
            # The advice also covers confirmed irreversible first-event order.
            # Match its semantic scope, not only the uncertainty in its source.
            needed.update("event:"+str(e.seq) for e in snapshot.history if e.evidence != "refuted"
                and e.seq > o.since and e.kind == "place" and e.object == o.clause.object and e.target == o.clause.target)
    else:
        raise ValueError("unregistered memory applicability rule")
    return frozenset(needed)


def tokens(text):
    return Counter(re.findall(r"[a-z_]+|[\u4e00-\u9fff]", text.lower()))


def similarity_topk(snapshot, instruction, memory, k=2):
    # A shared ordinary lexical cosine baseline. No task family, ID or labels.
    query = tokens(json.dumps({"instruction": instruction,
        "task": [o.clause.model_dump(exclude_none=True) for o in snapshot.contract.obligations],
        "state": asdict(snapshot.state), "uncertain_events": [
            {"kind": e.kind, "object": e.object, "target": e.target} for e in snapshot.history if e.evidence == "unknown"]},
        ensure_ascii=False))
    scores = []
    for item in memory:
        counts = tokens(item["title"]+" "+item["tags"]+" "+item["advice"])
        denominator = sqrt(sum(v*v for v in query.values())*sum(v*v for v in counts.values()))
        score = sum(v*counts[t] for t, v in query.items())/denominator if denominator else 0
        if score > 0:
            scores.append((score, item["id"], item))
    return tuple(item for _, _, item in sorted(scores, key=lambda x: (-x[0], x[1]))[:k])


def select(case, method, memory):
    if method not in METHODS:
        raise ValueError("unknown arm")
    snapshot = case["snapshot"].validate()
    records = all_records(snapshot)
    ids = set(records) if method in ("full", "full_memory") else set(dependency_ids(snapshot))
    selected = similarity_topk(snapshot, case["instruction"], memory) if method in (
        "full_memory", "filtered_memory", "joint") else ()
    initially_selected = tuple(m["id"] for m in selected)
    witnesses = {m["id"]: memory_witness(m, snapshot) for m in selected}
    if method == "joint":
        selected = tuple(m for m in selected if witnesses[m["id"]])
        for m in selected:
            ids.update(witnesses[m["id"]])
    ids = frozenset(ids)
    base_ids = dependency_ids(snapshot)
    return {"records": tuple(records[k] for k in records if k in ids),
        "memories": tuple({"id": m["id"], "title": m["title"], "advice": m["advice"]} for m in selected),
        "audit": {"all_records": len(records), "selected_records": len(ids),
            "selected_record_ids": sorted(ids), "retrieved_memory_ids": initially_selected,
            "selected_memory_ids": tuple(m["id"] for m in selected),
            "inapplicable_memory_count": sum(not witnesses[m["id"]] for m in selected),
            "extra_records_from_memory": sorted(ids-set(base_ids)) if method == "joint" else [],
            "joint_context_equals_strong_dependency_filter": ids == base_ids if method == "joint" else None,
            "all_task_clauses_preserved": True}}


RULES = """Plan a complete remaining suffix for ACCEPTED_TASK using supplied current observations and event evidence. The task is already accepted: do not add, remove, weaken or reinterpret clauses. Return only the specified JSON. Objects blue/yellow; targets table/green/return; one hand; green/return capacity one, table unlimited. pick requires empty hand and has target null. place requires holding that object and a free destination. Predicted actions are not actual events.
Clauses: at(object,target) requires final location; empty requires final empty hand; forbid(object) forbids any future pick/place and historical operations AFTER since; visit(object,target) requires a place event strictly AFTER since. before(first,second) requires the FIRST qualifying events of the referenced visit clauses in that order. Keep the program-supplied activation times. Unknown events may or may not have occurred; refuted events did not occur. Current state does not establish past order. All registered domain constraints and accepted clauses remain binding even if a memory suggests a different task. Omitted context is not proof that an unobserved fact is false or a location is table.
Memory advice is conditional and may be irrelevant; it never replaces current facts, task requirements or skill availability. You may ignore it. ready requires one legal suffix satisfying ALL clauses under EVERY possible evidence history, within the action limit. Empty ready actions are allowed for completed tasks. conflict means NO history alternative has a legal suffix within that bounded domain and action limit, not global physical impossibility. need_observation means no common valid suffix but at least one alternative is feasible. Return no actions for non-ready statuses. No code, markdown or explanations.
"""


def prompt(case, method, memory, feedback=None):
    chosen = select(case, method, memory)
    s = case["snapshot"]
    payload = {"INSTRUCTION": case["instruction"],
        "ACCEPTED_TASK": [{"clause": o.clause.model_dump(exclude_none=True), "since": o.since}
                          for o in s.contract.obligations],
        "OBSERVED_CONTEXT": chosen["records"], "EXPERIENCES": chosen["memories"],
        "cursor": s.cursor, "version": s.contract.version, "snapshot": s.digest,
        "CURRENT_OBSERVATION_ID": s.observation_id, "ACTION_LIMIT": HORIZON, "REQUEST_LIMIT": 2,
        "FEEDBACK": feedback, "OUTPUT_SCHEMA": PlanProposal.model_json_schema()}
    return RULES, json.dumps(payload, ensure_ascii=False), chosen["audit"]


def evaluate(snapshot, content):
    raw = PlanProposal.model_validate(decode_semantic_json(content))
    if raw.version != snapshot.contract.version or raw.snapshot != snapshot.digest:
        raise ValueError("stale response")
    if raw.status not in ("ready", "conflict", "need_observation"):
        raise ValueError("unknown status")
    plan = tuple(Event(snapshot.cursor+i+1, a.kind, a.object, a.target,
                       observation_id="prediction-only") for i, a in enumerate(raw.actions))
    decision = Decision(raw.status, plan, 0, 0, HORIZON)
    if raw.status == "ready":
        accepted = valid_suffix(snapshot, snapshot.contract, plan)
    else:
        accepted = not plan and decide(snapshot, snapshot.contract, HORIZON).status == raw.status
    feedback = None if accepted else {"error_code": "CANDIDATE_REJECTED",
        "diagnostic": explain_rejection(snapshot, snapshot.contract, decision)}
    return decision, feedback


def symbolic_replay(snapshot, decision):
    if decision.status != "ready":
        return {"task_complete": False, "dispatched_actions": 0, "physical_execution": False}
    runtime = RevisionRuntime(snapshot)
    remaining = decision.plan
    runtime.assess(remaining, allow_replan=False)
    trace = []
    from ch3.revision.history import successors
    from dataclasses import replace
    while remaining:
        command = runtime.consume(runtime.issue())
        state, event = next((ns, e) for ns, e in successors(runtime.snapshot.state, runtime.snapshot.cursor+1)
                           if (e.kind, e.object, e.target) == (remaining[0].kind, remaining[0].object, remaining[0].target))
        event = replace(event, observation_id=f"controlled-symbolic-{event.seq}")
        runtime.observe(state, observation_id=event.observation_id, events=(event,))
        trace.append({"command": command.model_dump(mode="json"), "observed_event": asdict(event)})
        remaining = remaining[1:]
        runtime.assess(remaining, allow_replan=False)
    complete = all(monitor(snapshot.contract, runtime.snapshot.state, h) == (frozenset(), frozenset())
                   for h in possible_histories(runtime.snapshot.history))
    return {"task_complete": complete, "dispatched_actions": len(trace), "trace": trace,
        "original_ledger_preserved": runtime.snapshot.history[:len(snapshot.history)] == snapshot.history,
        "physical_execution": False}
