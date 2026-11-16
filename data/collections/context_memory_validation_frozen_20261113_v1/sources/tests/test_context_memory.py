from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ch3.context_memory.cases import make_cases
from ch3.context_memory.core import (METHODS, PlanProposal, all_records, build_memory,
    dependency_ids, evaluate, memory_witness, prompt, select, symbolic_replay, verify_memory_sources)
from ch3.revision.history import decide, monitor, possible_histories


ROOT = Path(__file__).resolve().parents[1]
MEMORY = build_memory(ROOT)


def family(name):
    return next(c for c in make_cases() if c["family"] == name)


@pytest.mark.parametrize("case", make_cases()+make_cases("validation"), ids=lambda c: c["id"])
def test_preassigned_labels_and_actual_guarded_symbolic_replay(case):
    s = case["snapshot"]
    decision = decide(s, s.contract)
    assert decision.status == case["expected_status"]
    replay = symbolic_replay(s, decision)
    assert replay["task_complete"] == (decision.status == "ready")
    assert replay["physical_execution"] is False
    if decision.status == "ready":
        assert replay["original_ledger_preserved"]


@pytest.mark.parametrize("method", METHODS)
def test_prompt_does_not_depend_on_any_evaluation_annotation_or_arm_name(method):
    case = family("blocker")
    system, user, audit = prompt(case, method, MEMORY)
    changed = dict(case, family="SECRET", id="SECRET", expected_status="SECRET", split="SECRET", background_events=-99)
    assert prompt(changed, method, MEMORY) == (system, user, audit)
    assert "SECRET" not in user and "expected_status" not in user
    data = json.loads(user)
    assert len(data["ACCEPTED_TASK"]) == len(case["snapshot"].contract.obligations)
    assert data["ACTION_LIMIT"] == 8 and data["REQUEST_LIMIT"] == 2


def test_memories_have_replayable_sources_not_benchmark_answers():
    audit = verify_memory_sources(ROOT, MEMORY)
    assert len(audit) == 4 and sum(r["rejected"] for r in audit) == 2
    assert all(m["abstraction"].startswith("human_authored") for m in MEMORY)
    assert all("actions" not in m for m in MEMORY)


def test_strong_dependency_baseline_keeps_blockers_and_all_prohibitions():
    case = family("protected_blocker")
    selected = select(case, "filtered_memory", MEMORY)
    ids = set(selected["audit"]["selected_record_ids"])
    assert {"state:blue", "state:yellow", "state:holding"} <= ids
    data = json.loads(prompt(case, "filtered_memory", MEMORY)[1])
    assert any(o["clause"]["kind"] == "forbid" for o in data["ACCEPTED_TASK"])


def test_free_regions_are_observed_explicitly_when_bystander_location_is_omitted():
    case = family("normal")
    selected = select(case, "filtered", MEMORY)
    records = {r["id"]: r for r in selected["records"]}
    assert "state:yellow" not in records
    assert records["occupancy:green"]["occupants"] == ()
    assert records["occupancy:return"]["occupants"] == ()


@pytest.mark.parametrize("case", make_cases()+make_cases("validation"), ids=lambda c: c["id"])
def test_filtered_history_preserves_all_trace_monitor_results(case):
    s = case["snapshot"]
    ids = dependency_ids(s)
    # Compare all uncertainty branches, in matching enumeration order.
    for history in possible_histories(s.history):
        kept = tuple(e for e in history if "event:"+str(e.seq) in ids)
        assert monitor(s.contract, s.state, history) == monitor(s.contract, s.state, kept)
        d = decide(s, s.contract)
        assert monitor(s.contract, s.state, (*history, *d.plan)) == monitor(s.contract, s.state, (*kept, *d.plan))


def test_conditional_memory_does_not_treat_protection_as_permission():
    case = family("protected_blocker")
    chosen = select(case, "joint", MEMORY)
    assert chosen["memories"][0]["id"] == "occupied_destination"
    assert "does not authorize" in chosen["memories"][0]["advice"]
    assert decide(case["snapshot"], case["snapshot"].contract).status == "conflict"


def test_irrelevant_memories_suppressed_but_similarity_control_can_ignore_them():
    case = family("normal")
    assert not select(case, "joint", MEMORY)["memories"]
    assert select(case, "filtered_memory", MEMORY)["memories"]
    assert "may ignore" in prompt(case, "filtered_memory", MEMORY)[0]


def test_unknown_before_activation_does_not_trigger_current_order_memory():
    case = family("restart")
    item = next(m for m in MEMORY if m["id"] == "uncertain_first_event")
    assert not memory_witness(item, case["snapshot"])
    case = family("uncertain_order")
    assert memory_witness(item, case["snapshot"])


def test_shared_system_output_schema_and_task_binding_across_all_arms():
    case = family("normal")
    payloads = [json.loads(prompt(case, method, MEMORY)[1]) for method in METHODS]
    assert len({prompt(case, method, MEMORY)[0] for method in METHODS}) == 1
    variable = {"OBSERVED_CONTEXT", "EXPERIENCES"}
    assert all({k: v for k, v in p.items() if k not in variable} ==
               {k: v for k, v in payloads[0].items() if k not in variable} for p in payloads)


def test_no_claim_of_memory_driven_context_gain_when_strong_closure_is_identical():
    for case in make_cases()+make_cases("validation"):
        audit = select(case, "joint", MEMORY)["audit"]
        assert not audit["extra_records_from_memory"]
        assert audit["joint_context_equals_strong_dependency_filter"]


def output(case, decision):
    s = case["snapshot"]
    return json.dumps({"version": s.contract.version, "snapshot": s.digest, "status": decision.status,
        "actions": [{"kind": e.kind, "object": e.object, "target": e.target} for e in decision.plan]})


def test_wrong_ready_action_blocked_without_program_generated_repair():
    case = family("blocker")
    raw = {"version": 7, "snapshot": case["snapshot"].digest, "status": "ready", "actions": [
        {"kind": "place", "object": "blue", "target": "green"}]}
    decision, rejection = evaluate(case["snapshot"], json.dumps(raw))
    assert rejection["diagnostic"]["first_invalid_action"] == 1
    assert len(decision.plan) == 1  # not replaced by the solver's 5-action plan


def test_already_done_and_false_stop_checked_equally():
    case = family("already_done")
    raw = {"version": 7, "snapshot": case["snapshot"].digest, "status": "ready", "actions": []}
    decision, error = evaluate(case["snapshot"], json.dumps(raw))
    assert error is None and symbolic_replay(case["snapshot"], decision)["task_complete"]
    raw["status"] = "conflict"
    assert evaluate(case["snapshot"], json.dumps(raw))[1]


class ScriptedClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def complete(self, **kwargs):
        assert kwargs["max_tokens"] == 2048
        return SimpleNamespace(content=next(self.outputs), model="scripted-test", latency_ms=1,
            prompt_tokens=7, completion_tokens=3, total_tokens=10, finish_reason="stop", request_id=None, response_id=None)


def test_runner_counts_rejected_candidate_and_retries_with_shared_feedback():
    from scripts.benchmark_context_memory import run_trial, summarize
    case = family("blocker")
    bad = json.dumps({"version": 7, "snapshot": case["snapshot"].digest, "status": "ready", "actions": [
        {"kind": "place", "object": "blue", "target": "green"}]})
    good = output(case, decide(case["snapshot"], case["snapshot"].contract))
    row = run_trial(ScriptedClient([bad, good]), case, "joint", MEMORY)
    assert row["calls"] == 2 and row["tokens"] == 20 and not row["first_correct"]
    assert row["correct"] and row["dispatch"]["task_complete"]
    second = json.loads(row["attempts"][1]["user_prompt"])
    assert second["FEEDBACK"]["diagnostic"]["first_invalid_action"] == 1
    summary = summarize([row])["joint"]
    assert summary["rejected_candidates"] == summary["unsafe_ready_proposals"] == 1


def test_runner_keeps_failed_parsing_in_original_denominator():
    from scripts.benchmark_context_memory import run_trial, summarize
    case = family("normal")
    row = run_trial(ScriptedClient(["{", "[]"]), case, "full", MEMORY)
    assert row["calls"] == 2 and not row["accepted"] and "dispatch" not in row
    summary = summarize([row])["full"]
    assert summary["n"] == summary["feasible_cases"] == 1 and summary["final_correct"] == 0
    assert summary["errors"] == 2
