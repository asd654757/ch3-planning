#!/usr/bin/env python3
"""Plan-identity table: symbolic BFS suffix vs the frozen ROUTED suffix (Fair-100).

P1's headline for the two protocols is not "the symbolic planner wins end to end"
but "the LLM produced the same plan the search would have".  That claim lives or
dies on how the two plans compare field by field, so the comparison is a script
rather than a paragraph.  Read-only over frozen artifacts.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

FIELDS = ("skill", "object_id", "target_id", "arm")

def load_plans(path: Path, *, record_type: str, mode: str | None) -> dict:
    out: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") != record_type:
            continue
        if mode is not None and row.get("mode") != mode:
            continue
        actions = row.get("suffix_actions")
        if actions is None:
            plan = row.get("plan")
            if not plan:
                continue
            actions = plan["actions"]
        out[(str(row["task_id"]), int(row["seed"]))] = actions
    return out


def compare(symbolic: dict, reference: dict, *, fields: tuple[str, ...]) -> dict[str, Any]:
    keys = sorted(set(symbolic) & set(reference))
    projected = {
        tag: {k: [tuple(action[name] for name in fields) for action in plans] for k, plans in src.items()}
        for tag, src in (("symbolic", symbolic), ("reference", reference))
    }

    def same(key: tuple[str, int], tag: str) -> bool:
        return projected["symbolic"][key] == projected[tag][key]

    identical = [k for k in keys if same(k, "reference")]
    length_equal = [k for k in keys if len(symbolic[k]) == len(reference[k])]
    differing = [k for k in keys if k not in identical]
    arm_only = [
        k
        for k in differing
        if compare_fields(symbolic, reference, k, ("skill", "object_id", "target_id"))
    ]
    return {
        "points": len(keys),
        "fields": list(fields),
        "identical_action_count_rate": len(length_equal) / len(keys),
        "mean_actions_symbolic": sum(len(symbolic[k]) for k in keys) / len(keys),
        "mean_actions_reference": sum(len(reference[k]) for k in keys) / len(keys),
        "length_equal_points": len(length_equal),
        "fully_identical_points": len(identical),
        "arm_only_points": len(arm_only),
        "arm_only_first_skill": dict(
            Counter(symbolic[k][0]["skill"] for k in arm_only).most_common()
        ),
        "other_differences": sorted(
            f"{k[0]}/{k[1]}"
            for k in differing
            if k not in arm_only
        )[:20],
        "action_count_histogram_symbolic": dict(
            sorted(Counter(len(symbolic[k]) for k in keys).items())
        ),
        "action_count_histogram_reference": dict(
            sorted(Counter(len(reference[k]) for k in keys).items())
        ),
    }


def compare_fields(
    symbolic: dict, reference: dict, key: tuple[str, int], fields: tuple[str, ...]
) -> bool:
    def project(plans: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return [tuple(plan[name] for name in fields) for plan in plans]

    return project(symbolic[key]) == project(reference[key])


def arm_admissibility(
    keys: list[tuple[str, int]],
    symbolic: dict,
    reference: dict,
    *,
    source: str,
    tasks_path: str,
    registry_path: str,
) -> dict[str, Any]:
    """Score both arm choices of every arm-only point with the frozen evaluator.

    If the two plans differ only in ``arm`` and both pass validation and reach the
    goal, the acting arm is a tie the search broke by convention rather than a
    correctness difference.  Anything else would be a real disagreement.
    """
    from ch3.capability.registry import CapabilityRegistry
    from ch3.goal.goal_checker import goal_satisfied
    from ch3.schema.model_plan import GoalSpec, ModelPlan, ModelPlanAction
    from ch3.validator.pipeline import Validator
    from ch3.validator.result import ValidationResult
    from scripts.fair_external_baselines_v2 import state_from_record

    episodes = {
        (str(row["task_id"]), int(row["seed"])): row
        for row in json.load(Path(source).open(encoding="utf-8"))["episode_detail"]
    }
    tasks = {
        str(row["task_id"]): row
        for row in map(
            json.loads,
            (line for line in Path(tasks_path).read_text(encoding="utf-8").splitlines() if line.strip()),
        )
    }
    validator = Validator(scene_objects=set(), registry=CapabilityRegistry.from_yaml(registry_path))

    verdicts: dict[str, list[str]] = {
        "both_ok": [],
        "only_symbolic_arm_ok": [],
        "only_reference_arm_ok": [],
        "neither_ok": [],
    }
    missing = []
    details: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for key in sorted(keys):
        episode = episodes.get(key)
        task = tasks.get(key[0])
        if episode is None or task is None:
            missing.append(f"{key[0]}/{key[1]}")
            continue
        state = state_from_record(task, episode["attempts"][0])
        goal = GoalSpec.model_validate(task["goal"])
        validator.scene_objects = set(task["objects"])
        outcomes = {}
        for tag, plans in (("symbolic", symbolic), ("reference", reference)):
            suffix = [
                ModelPlanAction.model_validate(
                    {field: action[field] for field in FIELDS} | {"step_id": index + 1}
                )
                for index, action in enumerate(plans[key])
            ]
            if suffix:
                result = validator.validate(ModelPlan(actions=suffix), state)
            else:
                result = ValidationResult(valid=True, final_state=state.copy())
            final_state = result.final_state if result.valid else None
            goal_ok = bool(
                result.valid
                and goal_satisfied(final_state or state, goal, validator.registry.arms)
            )
            outcomes[tag] = {
                "valid": bool(result.valid),
                "goal": goal_ok,
                "accepted": bool(result.valid and goal_ok),
                "error_code": None
                if result.valid
                else str(getattr(result.error_code, "name", result.error_code)),
                "error_layer": None if result.valid else str(result.layer),
                "final_holding": sorted(
                    fact
                    for fact in (final_state.facts() if final_state else set())
                    if fact.startswith("holding(")
                ),
            }
        label = f"{key[0]}/{key[1]}"
        sym_ok = outcomes["symbolic"]["accepted"]
        ref_ok = outcomes["reference"]["accepted"]
        record = {
            "point": label,
            "first_skill": symbolic[key][0]["skill"],
            "symbolic_arm": symbolic[key][0]["arm"],
            "reference_arm": reference[key][0]["arm"],
            "symbolic": outcomes["symbolic"],
            "reference": outcomes["reference"],
        }
        details[label] = record
        if sym_ok and ref_ok:
            verdicts["both_ok"].append(label)
        elif sym_ok:
            verdicts["only_symbolic_arm_ok"].append(label)
        elif ref_ok:
            verdicts["only_reference_arm_ok"].append(label)
        else:
            verdicts["neither_ok"].append(label)
        if not (sym_ok and ref_ok):
            rejected.append(record)
    return {
        "arm_only_points": len(keys),
        "resolved": len(keys) - len(missing),
        "missing_source_records": missing[:20],
        "counts": {tag: len(items) for tag, items in verdicts.items()},
        "exceptions": {
            tag: items[:20] for tag, items in verdicts.items() if tag != "both_ok" and items
        },
        "arm_pairs": dict(
            Counter(
                f"{row['symbolic_arm']}->{row['reference_arm']}"
                for row in details.values()
            ).most_common()
        ),
        "first_skills": dict(Counter(row["first_skill"] for row in details.values()).most_common()),
        "rejected": rejected[:20],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbolic",
        required=True,
        help="symbolic_planner_cases.jsonl from a fair100 run",
    )
    parser.add_argument(
        "--reference",
        default="data/collections/fair_routed_v2_20260916_102135.jsonl",
    )
    parser.add_argument("--mode", default="BFS_EXEC")
    parser.add_argument("--reference-record-type", default="fair_routed_v2_case")
    parser.add_argument(
        "--source",
        default=(
            "data/collections/sim_closed_loop_visualstate_perturbed100_"
            "20260914_115417.json"
        ),
    )
    parser.add_argument("--tasks", default="data/scenarios/multiskill_tasks_v1.jsonl")
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument(
        "--check-arm-admissible",
        action="store_true",
        help="re-score the arm-only points with the frozen evaluator for both arms",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    symbolic = load_plans(
        Path(args.symbolic), record_type="symbolic_planner_case", mode=args.mode
    )
    reference = load_plans(
        Path(args.reference), record_type=args.reference_record_type, mode=None
    )
    triple = compare(symbolic, reference, fields=("skill", "object_id", "target_id"))
    quad = compare(symbolic, reference, fields=FIELDS)
    payload: dict[str, Any] = {
        "symbolic": args.symbolic,
        "reference": args.reference,
        "mode": args.mode,
        "triple_projection": triple,
        "quadruple_projection": quad,
    }
    if args.check_arm_admissible:
        differing = sorted(set(symbolic) & set(reference))
        differing = [
            key
            for key in differing
            if not compare_fields(symbolic, reference, key, FIELDS)
            and compare_fields(symbolic, reference, key, ("skill", "object_id", "target_id"))
        ]
        payload["arm_admissibility"] = arm_admissibility(
            differing,
            symbolic,
            reference,
            source=args.source,
            tasks_path=args.tasks,
            registry_path=args.registry,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
