#!/usr/bin/env python3
"""Taxonomy of the points the release-precondition rewrite could not save.

The counterfactual (``scripts/release_precondition_counterfactual.py``) recovers part of
ROUTED's wrong-held-object failures, so the remaining question is what the residual
actually is: another missing rule, or a plan that is incoherent no matter what runs
before it.  Read-only over the frozen artifacts and the counterfactual output.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

FAMILY_RULES = ("pick_place", "push", "press")


def action_shape(actions: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(str(a["skill"]), str(a.get("object_id")), str(a.get("arm"))) for a in actions]


def classify(case: dict[str, Any]) -> str:
    """First structural defect in the rewritten plan, walking it step by step.

    The order is deliberate: an incoherent action (pushing something the plan itself
    just picked up) is a different missing rule from a missing release, so it is
    checked before the release-related shapes.
    """
    after = case["after"]
    actions = case["after_actions"]
    held_after: dict[str, str] = {}
    for action in actions:
        arm, skill, obj = action["arm"], action["skill"], action.get("object_id")
        if skill == "pick":
            held_after[arm] = str(obj)
        elif skill == "place":
            held_after.pop(arm, None)
        elif skill in {"push", "press"}:
            if arm in held_after:
                if held_after[arm] == obj:
                    return "push_or_press_own_picked_object"
                return "push_or_press_while_holding_other"
    if not actions:
        return "empty_plan"
    if after["valid"] and not after["goal_satisfied"]:
        if case["observed_holding"] and str(actions[0]["skill"]) == "place":
            return "released_but_task_still_undone"
        return "valid_but_goal_not_reached"
    if case["observed_holding"] and str(actions[0]["skill"]) == "place":
        return "release_present_but_plan_still_rejected"
    if case["task_family"] in {"push", "press"} and set(
        str(a["skill"]) for a in actions
    ) <= {"pick", "place"}:
        return "wrong_skill_family"
    return "other_" + str(after.get("error_code") or "NONE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        default="data/reports/release_counterfactual_428_20260926/release_counterfactual_cases.jsonl",
    )
    parser.add_argument("--baseline", default="ROUTED")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    rows = [
        json.loads(line)
        for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mine = [r for r in rows if r["baseline"] == args.baseline]
    residual = [
        r
        for r in mine
        if not (r["after"]["valid"] and r["after"]["goal_satisfied"])
    ]

    by_defect = Counter(classify(r) for r in residual)
    table: dict[str, Any] = {}
    for defect in sorted(by_defect):
        subset = [r for r in residual if classify(r) == defect]
        table[defect] = {
            "points": len(subset),
            "by_perturbation": dict(
                Counter(r["perturbation_type"] for r in subset).most_common()
            ),
            "by_task_family": dict(
                Counter(r["task_family"] for r in subset).most_common()
            ),
            "by_error_code": dict(
                Counter(str(r["after"].get("error_code") or "NONE") for r in subset).most_common()
            ),
            "mean_suffix_actions": sum(
                len(r["after_actions"]) for r in subset
            ) / len(subset),
            "example": {
                "point_key": subset[0]["point_key"],
                "holding": subset[0]["observed_holding"],
                "plan": action_shape(subset[0]["after_actions"]),
                "error_code": subset[0]["after"].get("error_code"),
                "error_layer": subset[0]["after"].get("error_layer"),
            },
        }
    payload = {
        "record_type": "release_counterfactual_residual_taxonomy",
        "baseline": args.baseline,
        "cases": len(mine),
        "residual_points": len(residual),
        "residual_by_perturbation": dict(
            Counter(r["perturbation_type"] for r in residual).most_common()
        ),
        "by_defect": table,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
    print(json.dumps({"output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
