from dataclasses import replace

import pytest

from ch3.repair.recovery_decision import (
    ActionModel, Branch, Budget, Context, RecoveryDecisionPlanner, RecoveryModel,
)
from scripts.recovery_decision_diagnostic import context, run, synthetic_model


def test_information_is_worth_acquiring_only_when_it_changes_the_choice():
    informative = RecoveryDecisionPlanner(synthetic_model()).choose(context())
    uninformative = RecoveryDecisionPlanner(synthetic_model(informative=False)).choose(context())
    assert informative.action == "observe"
    assert uninformative.action == "replan"
    assert informative.values["observe"] > informative.values["replan"]
    assert uninformative.values["observe"] < uninformative.values["replan"]


def test_horizon_changes_decision_and_budget_prevents_free_future_actions():
    model = synthetic_model()
    assert RecoveryDecisionPlanner(model, depth=1).choose(context()).action == "replan"
    planner = RecoveryDecisionPlanner(model)
    assert planner.choose(context(time_units=1)).action == "replan"
    assert planner.choose(context(time_units=0)).action == "stop"
    assert "observe" not in planner.choose(context(observations=0)).values


def test_guard_permissions_are_never_bypassed_by_model_value():
    planner = RecoveryDecisionPlanner(synthetic_model())
    ctx = replace(context({"ready": 1}), enabled=frozenset({"stop"}))
    assert planner.choose(ctx).action == "stop"
    assert planner.choose(context({"unexplained": 1})).reason == "unsupported_hypothesis"


def test_actual_observation_updates_posterior_and_requires_fresh_guard():
    planner = RecoveryDecisionPlanner(synthetic_model())
    ctx = context()
    post = planner.update(ctx, "observe", observation="ready", observation_id="actual:1",
                          enabled=frozenset({"continue", "stop"}))
    assert post.belief == {"ready": 1}
    assert post.budget.observations == 0
    assert planner.choose(post).action == "continue"
    with pytest.raises(ValueError, match="fresh actual"):
        planner.update(ctx, "observe", observation="ready", observation_id=ctx.observation_id,
                       enabled=ctx.enabled)
    with pytest.raises(ValueError, match="unmodelled"):
        planner.update(ctx, "observe", observation="new_sensor_result", observation_id="actual:1",
                       enabled=ctx.enabled)


def test_identical_visible_failure_updates_hidden_failure_hypotheses():
    # Both explanations produce the SAME visible failure; Bayes, not a hidden
    # fixture label, changes retry value after repeated real observations.
    model = RecoveryModel({
        "retry": ActionModel(Budget(0, 1, 0, 1), .05, {
            "transient": (Branch(.8, "done", terminal="success"),
                          Branch(.2, "failed", next_state="transient")),
            "persistent": (Branch(.1, "done", terminal="success"),
                           Branch(.9, "failed", next_state="persistent")),
        }),
        "replan": ActionModel(Budget(0, 1, 1, 1), .1, {
            s: (Branch(.6, "done", terminal="success"),
                Branch(.4, "end", terminal="failure"))
            for s in ("transient", "persistent")
        }),
    }, {"transient": 0, "persistent": 0}, "synthetic_test", "zero_horizon")
    planner = RecoveryDecisionPlanner(model, depth=1)
    ctx = Context("actual:0", {"transient": .8, "persistent": .2}, Budget(0, 4, 1, 4),
                  frozenset({"retry", "replan", "stop"}))
    assert planner.choose(ctx).action == "retry"
    ctx = planner.update(ctx, "retry", observation="failed", observation_id="actual:1", enabled=ctx.enabled)
    assert ctx.belief["transient"] == pytest.approx(.16 / .34)
    assert planner.choose(ctx).action == "replan"


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_invalid_model_numbers_are_rejected(bad):
    with pytest.raises(ValueError):
        Branch(bad, "bad", terminal="failure")


def test_terminal_model_predictions_do_not_update_live_evidence():
    planner = RecoveryDecisionPlanner(synthetic_model())
    ctx = context({"ready": 1})
    with pytest.raises(ValueError, match="terminal observation"):
        planner.update(ctx, "continue", observation="goal_observed", observation_id="actual:1",
                       enabled=ctx.enabled)


def test_ambiguous_terminal_sensor_labels_are_rejected_across_states():
    action = ActionModel(Budget(0, 1, 0, 1), 0, {
        "a": (Branch(1, "same", terminal="success"),),
        "b": (Branch(1, "same", next_state="b"),),
    })
    with pytest.raises(ValueError, match="consistently"):
        RecoveryModel({"continue": action}, {"a": 0, "b": 0}, "test", "test")


def test_diagnostic_output_is_explicitly_synthetic():
    result = run()
    assert result["evidence_kind"] == "synthetic_algorithm_diagnostic_only"
    assert len(result["cases"]) == 6
    assert all("NOT_measured_success" in r["value_kind"] for r in result["cases"])
