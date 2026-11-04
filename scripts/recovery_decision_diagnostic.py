"""Synthetic arithmetic diagnostics, NOT robot/model performance experiments."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from ch3.repair.recovery_decision import (
    ActionModel, Branch, Budget, Context, RecoveryDecisionPlanner, RecoveryModel,
)


def synthetic_model(*, informative: bool = True) -> RecoveryModel:
    observation = {
        s: (Branch(1, s if informative else "unresolved", next_state=s),)
        for s in ("ready", "retryable")
    }
    return RecoveryModel(
        actions={
            "continue": ActionModel(Budget(0, 1, 0, 1), .05, {
                "ready": (Branch(.98, "goal_observed", terminal="success"),
                          Branch(.02, "execution_failed", terminal="failure")),
            }),
            "observe": ActionModel(Budget(1, 0, 0, 1), .05, observation),
            "retry": ActionModel(Budget(0, 1, 0, 1), .1, {
                "retryable": (Branch(.65, "goal_observed", terminal="success"),
                              Branch(.35, "retry_failed", next_state="retryable")),
            }),
            "replan": ActionModel(Budget(0, 1, 1, 1), .2, {
                s: (Branch(.7, "goal_observed", terminal="success"),
                    Branch(.3, "execution_failed", terminal="failure"))
                for s in ("ready", "retryable")
            }),
        }, leaf_success={"ready": 0, "retryable": 0},
        provenance="synthetic_hand_set_NOT_calibrated_NOT_empirical",
        leaf_provenance="zero_terminal_value_at_search_horizon; sensitivity_check_required",
    )


def context(belief=None, *, time_units=3, observations=1):
    return Context("synthetic:obs-0", belief or {"ready": .5, "retryable": .5},
                   Budget(observations, 2, 1, time_units),
                   frozenset({"continue", "observe", "retry", "replan", "stop"}))


def direct_policy(ctx, allowed):
    """A declared fixed replan control, NOT a strong LLM agent reproduction."""
    return "replan" if "replan" in allowed else "stop"


def observe_once_policy(ctx, allowed):
    if len([p for p in ctx.belief.values() if p > 0]) > 1 and "observe" in allowed:
        return "observe"
    for action in ("continue", "retry", "replan", "stop"):
        if action in allowed:
            return action


def run():
    cases = [
        ("informative_observation", synthetic_model(), context()),
        ("uninformative_observation", synthetic_model(informative=False), context()),
        ("known_ready", synthetic_model(), context({"ready": 1})),
        ("known_retryable", synthetic_model(), context({"retryable": 1})),
        ("time_only_for_one_action", synthetic_model(), context(time_units=1)),
        ("unmodelled_hypothesis", synthetic_model(), context({"other": 1})),
    ]
    rows = []
    for name, model, ctx in cases:
        planner = RecoveryDecisionPlanner(model)
        decision = planner.choose(ctx)
        rows.append(dict(case=name, decision=asdict(decision),
            fixed_replan_model_value=planner.evaluate_policy(ctx, direct_policy),
            observe_once_model_value=planner.evaluate_policy(ctx, observe_once_policy),
            value_kind="synthetic_expected_utility_NOT_measured_success"))
    return dict(evidence_kind="synthetic_algorithm_diagnostic_only", cases=rows,
        limitations=["Hand-set probabilities; no calibration or empirical superiority.",
                     "No model API calls, simulator execution or supervisor integration.",
                     "Fixed controls are arithmetic checks, not tuned research baselines.",
                     "Runtime completion always requires independent goal evidence."])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(run(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(result + "\n")
    print(result)
