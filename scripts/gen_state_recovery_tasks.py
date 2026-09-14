#!/usr/bin/env python3
"""Freeze the execution-state recovery task set.

The benchmark deliberately derives all variants from one MultiSkill task so
that arms see the same nominal task and frozen plan.  Each perturbation
describes one possible *real* world after the executed prefix:
- nominal_state: the prefix had its intended effect;
- grasp_failure: the primitive did not establish its intended effect;
- object_displacement: an external disturbance reverted the completed effect;
- wrong_held_object: the arm holds a decoy instead of the task object.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

PERTURBATIONS = (
    "nominal_state",
    "grasp_failure",
    "object_displacement",
    "wrong_held_object",
)
SCHEMA_VERSION = "2026-09-14-state-recovery-tasks-v1"


def decoy_for(task: Mapping[str, Any], tasks: list[dict[str, Any]]) -> str:
    """Pick a plausible scene decoy from another task of the same family."""
    family = task["task_family"]
    candidates = [
        obj
        for other in tasks
        if other["task_family"] == family and other["task_id"] != task["task_id"]
        for obj in other["objects"]
        if obj not in task["objects"] and obj != "goal_pad"
    ]
    if not candidates:
        # A stable fallback is preferable to silently omitting the decoy.
        return f"decoy_{family}_object"
    idx = int(str(task["task_id"])[-3:]) % len(candidates)
    return candidates[idx]


def make_variant(task: dict[str, Any], tasks: list[dict[str, Any]], perturbation: str) -> dict[str, Any]:
    out = json.loads(json.dumps(task, ensure_ascii=False))
    source_id = task["task_id"]
    out["source_task_id"] = source_id
    out["perturbation_type"] = perturbation
    out["task_id"] = f"{source_id}__{perturbation}"
    out["schema_version"] = SCHEMA_VERSION
    out["benchmark"] = "state_recovery_v1"

    family = task["task_family"]
    if perturbation == "nominal_state":
        out["prefix_length_rule"] = "full"
    elif perturbation == "grasp_failure":
        # A failed pick leaves the first action executed but ineffective.
        # Push/press are single-action plans, so the whole plan is the failed
        # primitive and must be regenerated from the observed state.
        out["prefix_length_rule"] = "first" if family == "pick_place" else "full"
    elif perturbation == "object_displacement":
        out["prefix_length_rule"] = "full"
    elif perturbation == "wrong_held_object":
        # For manipulation, the failed pick leaves the wrong object in hand.
        # For push/press, the observed anomaly exists before the first action.
        out["prefix_length_rule"] = "first" if family == "pick_place" else "none"
        decoy = decoy_for(task, tasks)
        out["decoy_object"] = decoy
        out["objects"] = sorted(set(task["objects"]) | {decoy})
        out["initial_state"]["at"][decoy] = "table"
    else:
        raise ValueError(perturbation)

    if perturbation == "wrong_held_object":
        arm = "right" if family in {"pick_place", "push"} else "left"
        out["wrong_held_arm"] = arm
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/scenarios/multiskill_tasks_v1.jsonl")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source_path = Path(args.source)
    tasks = [json.loads(line) for line in source_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    variants = [make_variant(task, tasks, p) for task in tasks for p in PERTURBATIONS]
    output_path = Path(args.output)
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite frozen set: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in variants:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({
        "source": str(source_path),
        "tasks": len(tasks),
        "perturbations": len(PERTURBATIONS),
        "variants": len(variants),
        "output": str(output_path),
        "schema_version": SCHEMA_VERSION,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
