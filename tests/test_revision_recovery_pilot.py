from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from ch3.revision.evaluation import score, valid_suffix
from ch3.revision.history import Clause, Contract, Event, Obligation, State, decide
from ch3.revision.recovery_pilot import METHODS, HORIZON, candidate, make_pilot_cases, prompt
from ch3.revision.runtime import RevisionRuntime, observed_state, routed_decision
from ch3.state.world_state import WorldState


def by_family(name):
    return next(c for c in make_pilot_cases() if c["family"] == name)


@pytest.mark.parametrize("case", make_pilot_cases(), ids=lambda c: c["id"])
def test_explicit_development_labels_and_router(case):
    decision = decide(case["snapshot"], case["gold"], HORIZON)
    assert decision.status == case["gold_status"]
    assert score(case, case["gold"], decision)["end_to_end"]
    actual, route = routed_decision(case["snapshot"], case["gold"], case["old_suffix"], HORIZON)
    assert actual.status == decision.status
    if actual.status == "ready":
        assert valid_suffix(case["snapshot"], case["gold"], actual.plan)


@pytest.mark.parametrize("method", METHODS)
def test_all_factual_inputs_equal_and_gold_independent(method):
    case = by_family("past_order")
    system, text = prompt(case, method)
    altered = dict(case, id="SECRET", family="SECRET", gold=Contract(99, ()), gold_status="SECRET")
    assert prompt(altered, method) == (system, text)
    data = json.loads(text)
    baseline = json.loads(prompt(case, "direct")[1])
    ignored = {"OUTPUT_SCHEMA", "OUTPUT_INSTRUCTION"}
    assert {k: v for k, v in data.items() if k not in ignored} == {k: v for k, v in baseline.items() if k not in ignored}
    assert "SECRET" not in text
    if method == "direct":
        assert "Do not output plans" not in system


def test_state_adapter_requires_complete_evidence_and_never_defaults_unknown():
    world = WorldState.table_scene(["blue_candidate", "yellow_candidate"])
    coverage = set(world.objects)
    with pytest.raises(ValueError):
        observed_state(world, supported_objects=coverage, holding_supported=False)
    world.at["blue_candidate"] = "observed_support"
    with pytest.raises(ValueError):
        observed_state(world, supported_objects=coverage, holding_supported=True)
    world.at["blue_candidate"] = "green_region"
    assert observed_state(world, supported_objects=coverage, holding_supported=True) == State("green")


def test_lost_effect_requires_compensation_without_rewriting_old_pick():
    case = by_family("effect_lost")
    runtime = RevisionRuntime(case["snapshot"])
    decision, route = runtime.accept(case["gold"], expected_digest=runtime.snapshot.digest, expected_generation=runtime.generation,
                                      old_suffix=case["old_suffix"])
    assert route == "BOUNDED_REPLAN" and decision.plan[0].kind == "pick"
    old_history = runtime.snapshot.history
    ticket = runtime.issue()
    action = runtime.consume(ticket)
    assert action.object_id == "blue_candidate" and action.skill.value == "pick"
    assert runtime.snapshot.history == old_history and runtime.snapshot.state == State()
    with pytest.raises(ValueError):
        runtime.consume(ticket)
    with pytest.raises(ValueError):
        runtime.issue()
    # A fresh observation, not the command receipt, supplies this new event.
    runtime.observe(State("hand", holding="blue"), observation_id="obs-new",
                    events=(Event(2, "pick", "blue", None, observation_id="obs-new"),))
    runtime.assess(decision.plan[1:], allow_replan=False)
    assert runtime.consume(runtime.issue()).skill.value == "place"
    assert runtime.snapshot.history[:1] == old_history


def test_current_observation_invalidates_ticket_even_identical_state_and_id():
    case = by_family("normal")
    runtime = RevisionRuntime(case["snapshot"])
    runtime.accept(case["gold"], expected_digest=runtime.snapshot.digest, expected_generation=runtime.generation,
                   old_suffix=case["old_suffix"])
    ticket = runtime.issue()
    runtime.observe(runtime.snapshot.state, observation_id=runtime.snapshot.observation_id)
    with pytest.raises(ValueError):
        runtime.consume(ticket)
    with pytest.raises(ValueError):
        runtime.accept(Contract(2, runtime.snapshot.contract.obligations),
            expected_digest=runtime.snapshot.digest, expected_generation=ticket.generation)


def test_history_cannot_be_overwritten_and_stale_task_is_rejected():
    case = by_family("normal")
    runtime = RevisionRuntime(case["snapshot"])
    old = runtime.snapshot
    with pytest.raises(ValueError):
        runtime.observe(State(), observation_id="other", events=(Event(1, "place", "blue", "table"),))
    assert runtime.snapshot == old
    runtime.observe(State(), observation_id="changed")
    with pytest.raises(ValueError):
        runtime.accept(case["gold"], expected_digest=old.digest, expected_generation=0)


def test_conflicting_intent_suspends_old_plan_and_preserves_ledger():
    case = by_family("held_prohibition")
    runtime = RevisionRuntime(case["snapshot"])
    decision, route = runtime.accept(case["gold"], expected_digest=runtime.snapshot.digest, expected_generation=runtime.generation)
    assert decision.status == "conflict" and runtime.pending
    assert runtime.snapshot.history == case["snapshot"].history
    with pytest.raises(ValueError):
        runtime.issue()


def direct_json(case, status="ready", actions=()):
    return json.dumps({"version": case["snapshot"].contract.version, "snapshot": case["snapshot"].digest,
        "clauses": [o.clause.model_dump(exclude_none=True) for o in case["gold"].obligations],
        "status": status, "actions": list(actions)})


def test_direct_invalid_plan_is_blocked_and_never_repaired():
    case = by_family("effect_lost")
    _, decision, _, error = candidate(case, "direct", direct_json(case, actions=[
        {"kind": "place", "object": "blue", "target": "green"}]))
    assert error and len(decision.plan) == 1 and decision.plan[0].kind == "place"
    runtime = RevisionRuntime(case["snapshot"])
    with pytest.raises(ValueError):
        runtime.accept(case["gold"], expected_digest=runtime.snapshot.digest, expected_generation=runtime.generation,
                       supplied_decision=decision)
    assert runtime.snapshot == case["snapshot"]


def test_direct_false_stop_rejected_and_empty_completion_supported():
    case = by_family("normal")
    assert candidate(case, "direct", direct_json(case, "conflict"))[3]
    done = by_family("already_done")
    assert candidate(done, "direct", direct_json(done))[3] is None


def test_external_suffix_assessment_does_not_call_solver_to_fix_it():
    case = by_family("effect_lost")
    runtime = RevisionRuntime(case["snapshot"])
    with pytest.raises(ValueError):
        runtime.assess(case["old_suffix"], allow_replan=False)
    assert runtime.decision is None


def test_global_blocker_needs_more_than_local_place_repair():
    case = by_family("blocker")
    decision, route = routed_decision(case["snapshot"], case["gold"], case["old_suffix"])
    assert route == "BOUNDED_REPLAN" and len(decision.plan) == 5
    assert decision.plan[0].kind == "place" and decision.plan[0].target == "table"


class ScriptedClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def complete(self, **kwargs):
        return SimpleNamespace(content=next(self.outputs), model="test-fixture", latency_ms=1,
            prompt_tokens=5, completion_tokens=5, total_tokens=10, finish_reason="stop",
            request_id=None, response_id=None)


def test_retry_counts_failed_proposal_and_dispatches_only_second_valid_suffix():
    from scripts.benchmark_revision_recovery_pilot import run_trial, summarize
    case = by_family("effect_lost")
    bad = direct_json(case, actions=[{"kind": "place", "object": "blue", "target": "green"}])
    good = direct_json(case, actions=[{"kind": "pick", "object": "blue"},
                                     {"kind": "place", "object": "blue", "target": "green"}])
    row = run_trial(ScriptedClient([bad, good]), case, "direct")
    assert row["accepted"] and not row["first_pass"]
    assert row["calls"] == 2 and row["tokens"] == 20
    assert row["dispatch"]["task_complete"] and row["dispatch"]["dispatched_actions"] == 2
    summary = summarize([row])["direct"]
    assert summary["rejected_candidates"] == 1 and summary["unsafe_ready_proposals"] == 1
    assert summary["symbolic_task_complete"] == 1


def test_parse_failures_remain_in_denominator_and_never_dispatch():
    from scripts.benchmark_revision_recovery_pilot import run_trial, summarize
    case = by_family("normal")
    row = run_trial(ScriptedClient(["{", "[]"]), case, "direct")
    assert not row["accepted"] and row["calls"] == 2 and "dispatch" not in row
    summary = summarize([row])["direct"]
    assert summary["n"] == summary["feasible_cases"] == 1
    assert summary["errors"] == 2 and summary["end_to_end"] == 0


def test_blocked_action_is_not_counted_as_a_translation_failure():
    from scripts.benchmark_revision_recovery_pilot import run_trial, summarize
    case = by_family("blocker")
    bad = direct_json(case, actions=[{"kind": "place", "object": "blue", "target": "green"}])
    row = run_trial(ScriptedClient([bad, bad]), case, "direct")
    summary = summarize([row])["direct"]
    assert summary["semantic_exact"] == summary["status_correct"] == 1
    assert summary["end_to_end"] == summary["dispatched_actions"] == 0


def test_strong_diagnostic_has_offending_state_but_no_reference_suffix():
    from ch3.revision.feedback import explain_rejection
    case = by_family("blocker")
    proposed, decision, _, _ = candidate(case, "direct", direct_json(case, actions=[
        {"kind": "place", "object": "blue", "target": "green"}]))
    diagnostic = explain_rejection(case["snapshot"], proposed, decision)
    assert diagnostic["type"] == "ACTION_PRECONDITION"
    assert diagnostic["first_invalid_action"] == 1
    assert diagnostic["state_before_action"]["yellow"] == "green"
    assert "plan" not in diagnostic and "gold" not in diagnostic


def test_strong_history_diagnostic_reports_feasible_alternatives_without_plan():
    from ch3.revision.feedback import explain_rejection
    case = by_family("uncertain_order")
    proposed, decision, _, _ = candidate(case, "direct", direct_json(case, "conflict"))
    diagnostic = explain_rejection(case["snapshot"], proposed, decision)
    assert diagnostic["history_alternatives"] == 2
    assert diagnostic["feasible_alternatives_within_budget"] == 1
    assert "plan" not in diagnostic


@pytest.mark.parametrize("case", make_pilot_cases(), ids=lambda c: c["id"])
def test_symbolic_dispatch_uses_supplied_suffix_and_keeps_nonready_separate(case):
    from scripts.benchmark_revision_recovery_pilot import symbolic_dispatch
    decision = decide(case["snapshot"], case["gold"], HORIZON)
    result = symbolic_dispatch(case, case["gold"], decision)
    assert result["dispatched_actions"] == len(decision.plan)
    assert result["task_complete"] == (case["gold_status"] == "ready")
    assert result["physical_execution"] is False
