from dataclasses import replace
import json

import pytest

from ch3.revision.cases import make_cases
from ch3.revision.evaluation import impact_set, mechanism_ablation, score, valid_suffix
from ch3.revision.history import (
    Clause, Contract, Obligation, Replacement, SlicedReplacement,
    apply_replacement, apply_sliced_replacement, decide, semantic_key,
)
from ch3.revision.model import METHODS, build_prompt, parse_output


@pytest.mark.parametrize("case", make_cases("dev")+make_cases("heldout"), ids=lambda c: c["id"])
def test_manual_gold_and_suffix(case):
    decision = decide(case["snapshot"], case["gold"])
    assert decision.status == case["gold_status"]
    result = score(case, case["gold"], decision)
    assert result["end_to_end"]
    assert result["preserved"] == result["preservation_total"]
    if decision.status == "ready":
        assert valid_suffix(case["snapshot"], case["gold"], decision.plan)


@pytest.mark.parametrize("method", METHODS)
def test_prompt_contains_no_gold_label_or_family(method):
    case = make_cases()[2]
    system, user = build_prompt(case, method)
    altered = dict(case, gold=Contract(99, ()), gold_status="wrong_gold", family="secretfamily", id="secretid")
    assert build_prompt(altered, method) == (system, user)
    assert case["family"] not in user
    assert "gold_status" not in user
    assert case["id"] not in user


def test_dependency_impact_includes_unchanged_order():
    case = next(c for c in make_cases() if c["family"] == "changed_waypoint_order")
    assert impact_set(case["snapshot"].contract, case["gold"]) == ("order", "va")
    # The unchanged order text is affected through its waypoint dependency;
    # an equivalent ID rewrite must not be scored as losing an untouched clause.
    d = decide(case["snapshot"], case["gold"])
    assert score(case, case["gold"], d)["preservation_total"] == 2


def test_strong_control_restores_accidentally_omitted_unedited_goal():
    case = make_cases()[0]
    s = case["snapshot"]
    only_changed = [o.clause for o in case["gold"].obligations if o.clause.id == "ga"]
    full = apply_replacement(s, Replacement(version=0, snapshot=s.digest, clauses=only_changed))
    sliced = apply_sliced_replacement(s, SlicedReplacement(version=0, snapshot=s.digest,
                    clauses=only_changed, changed_ids=["ga"]))
    assert semantic_key(full) != semantic_key(case["gold"])
    assert semantic_key(sliced) == semantic_key(case["gold"])


def test_ablation_exposes_irreversible_order_error():
    case = next(c for c in make_cases() if c["family"] == "past_order")
    a = mechanism_ablation(case)
    assert a["full_status"] == "conflict"
    assert a["no_history_status"] == "ready"
    assert a["final_only_status"] == "ready"


def test_ignored_history_changes_more_than_final_positions():
    case = next(c for c in make_cases() if c["family"] == "uncertain_order")
    a = mechanism_ablation(case)
    assert a["full_status"] == "need_observation"
    assert a["no_history_status"] == "ready"


def test_unsafe_suffix_is_not_success_even_when_decision_says_ready():
    case = make_cases()[0]
    d = decide(case["snapshot"], case["gold"])
    d = replace(d, plan=())
    result = score(case, case["gold"], d)
    assert result["unsafe_ready"] and not result["end_to_end"]
