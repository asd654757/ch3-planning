"""Matched-information semantic translators; no gold annotations in prompts."""
from dataclasses import asdict
import json

from ch3.revision.history import (Clause, Patch, Replacement, SlicedReplacement,
    apply_patch, apply_replacement, apply_sliced_replacement)
from ch3.revision.cases import json_default
from ch3.vlm.task_semantics import decode_semantic_json

METHODS = ("delta", "full", "full_sliced")
RULES = """You translate a natural-language incremental task update in a bounded robot domain.
INITIAL_TASK is the entire accepted task, including unmentioned requirements. Preserve them unless the update changes/cancels them. Do NOT silently weaken infeasible requirements. Do not output plans or predict feasibility: a shared checker handles that after translation.
Objects blue/yellow. Targets table/green/return. One hand, region green/return capacity one, table unlimited. Evidence is a partial append-only event ledger; current state is a separate fresh observation. Unknown events are NOT confirmed physical occurrences. You may not edit history or use gold labels.
Clause kinds: at(object,target) final position; empty no fields final empty hand; forbid(object) forbids all pick/place actions on that object after activation; visit(object,target) requires a place event after activation; before(first,second) references two visit clause IDs and requires their FIRST qualifying events in that order.
Keep existing IDs for unchanged clauses. An unchanged ID and content preserves activation time, even for full translation. New/changed clause content starts AFTER current history cursor. New before clauses can reference existing visit clauses: these retain original anchors, so already happened events can conflict with a new order. Never rewrite history to fix this.
To require doing the SAME visit AGAIN from now: REMOVE old visit, ADD visit with a NEW ID (e.g. va_again), and update any relevant before references. Replacing with identical ID+content does NOT reset time. New forbid is prospective; old unrelated events do not violate it. Explicitly cancelling an old prohibition removes that clause but never deletes events.
Return only the specified JSON schema, using version/snapshot from input. Clause fields other than id/kind that do not apply must be omitted or null. Do not include since: activation is assigned by the program. Any nonconflicting ID may be chosen for new clauses. Source quotes establish provenance only.
"""


def build_prompt(case, method):
    if method not in METHODS:
        raise ValueError("unknown method")
    s = case["snapshot"]
    schema = {"delta": Patch, "full": Replacement, "full_sliced": SlicedReplacement}[method]
    instruction = {
        "delta": "Return only edits (add/replace/remove). Each edit has id, quote (exact substring of UPDATE), and clause (null for remove). Unedited clauses are preserved by the program. Include dependent edits when necessary.",
        "full": "Return the COMPLETE revised clauses array, including every unchanged requirement. You can directly copy accepted unchanged clauses. Missing clauses are removed.",
        "full_sliced": "Return the COMPLETE revised clauses array AND changed_ids: precisely the IDs intentionally added, changed or removed. The program preserves every ID outside changed_ids from INITIAL_TASK, and applies only declared changes from your full translation. Include dependency edits. Removed IDs are listed in changed_ids and absent from clauses.",
    }[method]
    payload = {"INITIAL_INSTRUCTION": case["initial_instruction"],
        "INITIAL_TASK": [{"clause": o.clause.model_dump(exclude_none=True), "since": o.since}
                         for o in s.contract.obligations],
        "UPDATE": case["update"], "CURRENT_OBSERVED_STATE": asdict(s.state),
        "HISTORY": [asdict(e) for e in s.history], "observation_id": s.observation_id,
        "version": s.contract.version, "snapshot": s.digest, "cursor": s.cursor,
        "OUTPUT_INSTRUCTION": instruction, "OUTPUT_SCHEMA": schema.model_json_schema()}
    return RULES, json.dumps(payload, ensure_ascii=False, default=json_default)


def parse_output(case, method, content):
    raw = decode_semantic_json(content)
    s = case["snapshot"]
    if method == "delta":
        return apply_patch(s, Patch.model_validate(raw), case["update"])
    if method == "full":
        return apply_replacement(s, Replacement.model_validate(raw))
    if method == "full_sliced":
        return apply_sliced_replacement(s, SlicedReplacement.model_validate(raw))
    raise ValueError("unknown method")
