from dataclasses import replace

import pytest

from ch3.repair.recovery_decision import (
    ActionModel, Branch, Budget, Context, RecoveryDecisionPlanner, RecoveryModel,
)
from scripts.pilot_budgeted_recovery import fit_model, fixed_rule


def sensing_model():
    return RecoveryModel({
        "observe": ActionModel(Budget(1,0,0,1), 0, {
            "unknown": (Branch(1,"positive_rgb",next_state="ready"),)}),
        "continue": ActionModel(Budget(0,1,0,2), 0, {
            "ready": (Branch(1,"end",terminal="success"),)}),
    }, {"unknown":0,"ready":1}, "synthetic_semantic_test", "declared_leaf",
       {"positive_rgb":frozenset({"continue","stop"})}, {"ready":Budget(0,1,0,2)})


def test_observation_can_unlock_hypothetical_action_but_not_actual_dispatch():
    planner=RecoveryDecisionPlanner(sensing_model())
    ctx=Context("actual:0",{"unknown":1.},Budget(1,1,0,3),frozenset({"observe","stop"}))
    assert planner.choose(ctx).action=="observe"
    assert "continue" not in planner.admissible(ctx)
    # A real guard may reject the anticipated evidence; prediction cannot override it.
    updated=planner.update(ctx,"observe",observation="positive_rgb",observation_id="actual:1",
                           enabled=frozenset({"stop"}))
    assert planner.choose(updated).action=="stop"


def test_search_boundary_cannot_claim_success_without_completion_resources():
    planner=RecoveryDecisionPlanner(sensing_model(),depth=1)
    ctx=Context("actual:0",{"unknown":1.},Budget(1,1,0,2),frozenset({"observe","stop"}))
    assert planner.choose(ctx).action=="stop"
    assert planner.choose(replace(ctx,budget=Budget(1,1,0,3))).action=="observe"


def test_partial_development_failures_are_not_silently_dropped_in_model_fit():
    with pytest.raises(ValueError,match="failure-inclusive"):
        fit_model([{"status":"macro_cap_exceeded"}],"test")


def test_missing_macro_cannot_be_filled_with_synthetic_success():
    with pytest.raises(ValueError,match="lacks a supported"):
        fit_model([{"status":"complete","task_success":False,"decisions":[]}],"test")


def test_permission_model_requires_known_action_names():
    with pytest.raises(ValueError,match="permission projection"):
        RecoveryModel({}, {"a":0.}, "test", "test", {"event":frozenset({"invent_skill"})})


def test_rule_stops_when_ready_but_budget_does_not_allow_execution():
    ctx=Context("actual:1",{"ready":1.},Budget(0,0,0,0),frozenset({"stop"}))
    assert fixed_rule(ctx,("stop",),3)=="stop"
