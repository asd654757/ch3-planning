#!/usr/bin/env python3
"""P2 counterfactual: what a hard release precondition would have bought, for free.

Motivation from the frozen 428 sweep: 120 points that the symbolic planner solves
and ROUTED does not are all `wrong_held_object` / `grasp_failure`, 113 of them with a
single emitted action, first error `ARM_NOT_EMPTY` or `OBJECT_NOT_HELD`.  The reading
is that the LLM plans as if the hand were empty.  This script does not train or call
anything: it takes each arm's *frozen* suffix, inserts the release step that the
missing precondition would force, and re-scores with the frozen evaluator.

Rule (one sentence): for every arm that the observed state says is holding X, if the
plan's next action on that arm is not already `place(X, ...)`, emit
`place(arm, X -> table)` right before it.

Reported per baseline: recovery success before/after, paired exact McNemar, action
count delta, residual failure decomposition, and -- because the release step changes
plan shape -- how many recovered points stop satisfying the frozen one-episode
executable shape filter.  Nothing in data/collections is modified.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import GoalSpec, ModelPlanAction
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from scripts.state_recovery_offline_analysis import (
    mcnemar_exact_p,
    read_jsonl,
    state_from_facts,
    write_json,
    write_jsonl,
)
from scripts.symbolic_planner_baseline import (
    ARMS,
    evaluate_suffix,
    is_sim_executable,
)

BASELINES = ("ROUTED", "R2_STATE", "R1_FROM_STATE")
RELEASE_TARGET = "table"
HOLDING_PREFIX = "holding("


def parse_holding(facts: list[str]) -> dict[str, str]:
    """arm -> held object, from the observed fact set."""
    out: dict[str, str] = {}
    for fact in facts:
        if not fact.startswith(HOLDING_PREFIX):
            continue
        inner = fact[len(HOLDING_PREFIX):-1]
        arm, _, obj = inner.partition(", ")
        if arm and obj:
            out[arm] = obj
    return out


def insert_releases(
    actions: list[dict[str, Any]], holding: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (rewritten plan, inserted steps).

    Insertion is positional: the release for ``arm`` goes before the first remaining
    action that uses ``arm`` (or at the end if the plan never touches that arm -- a
    dangling hold that the completeness rule would otherwise reject).
    """
    remaining = [dict(action) for action in actions]
    inserted: list[dict[str, Any]] = []
    for arm in sorted(holding):
        obj = holding[arm]
        index = next(
            (
                i
                for i, action in enumerate(remaining)
                if action.get("arm") == arm
                and not (action.get("skill") == "place" and action.get("object_id") == obj)
            ),
            None,
        )
        if index is None:
            # The plan never acts with this arm again; a release is only needed if the
            # arm still ends up holding something, which the caller scores anyway.
            continue
        already = next(
            (
                i
                for i, action in enumerate(remaining[:index])
                if action.get("skill") == "place"
                and action.get("arm") == arm
                and action.get("object_id") == obj
            ),
            None,
        )
        if already is not None:
            continue
        release = {
            "skill": "place",
            "object_id": obj,
            "target_id": RELEASE_TARGET,
            "arm": arm,
            "inserted_by": "release_precondition",
        }
        remaining.insert(index, release)
        inserted.append(release | {"position": index})
    renumbered = [
        {**action, "step_id": i + 1} for i, action in enumerate(remaining)
    ]
    return renumbered, inserted


def plan_evaluation(
    actions: list[dict[str, Any]],
    *,
    validator: Validator,
    state: WorldState,
    goal: GoalSpec,
) -> dict[str, Any]:
    if not actions:
        return {
            "action_count": 0,
            "valid": False,
            "goal_satisfied": False,
            "error_code": "EMPTY_PLAN",
            "error_layer": "schema",
            "shape_ok": False,
        }
    suffix = [ModelPlanAction.model_validate(item) for item in actions]
    valid, goal_ok, _holds, _facts = evaluate_suffix(
        suffix=suffix, validator=validator, state=state, goal=goal
    )
    return {
        "action_count": len(actions),
        "valid": bool(valid),
        "goal_satisfied": bool(goal_ok),
        "shape_ok": bool(is_sim_executable([dict(a) for a in actions])),
    }


def load_points(
    formal_path: Path, scenarios_path: Path, baselines: tuple[str, ...]
) -> dict[tuple[str, int, str], dict[str, Any]]:
    scenarios = {row["task_id"]: row for row in read_jsonl(scenarios_path)}
    points: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in read_jsonl(formal_path):
        baseline = str(row.get("baseline", ""))
        if baseline not in baselines:
            continue
        key = (
            str(row["source_task_id"]),
            int(row["seed"]),
            str(row["perturbation_type"]),
        )
        slot = points.setdefault(key, {})
        scenario = scenarios.get(f"{key[0]}__{key[2]}")
        if scenario is None:
            raise RuntimeError(f"missing scenario for {key}")
        plan = row.get("model_plan") or {}
        slot[baseline] = {
            "actions": [dict(a) for a in plan.get("actions", [])],
            "observed_state_facts": sorted(row["observed_state_facts"]),
            "frozen_valid": bool(row["valid"]),
            "frozen_goal": bool(row["goal_satisfied"]),
            "frozen_recovery_success": bool(row["recovery_success"]),
            "frozen_error_code": row.get("error_code"),
            "frozen_error_layer": row.get("error_layer"),
            "frozen_suffix_action_count": row.get("suffix_action_count"),
            "scenario": scenario,
        }
    return {k: v for k, v in points.items() if len(v) == len(baselines)}


def run(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    points = load_points(Path(args.formal), Path(args.scenarios), BASELINES)
    selected = sorted(points.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))
    if args.limit:
        selected = selected[: args.limit]

    cases: list[dict[str, Any]] = []
    for key, per_baseline in selected:
        source_task_id, seed, perturbation = key
        scenario = next(iter(per_baseline.values()))["scenario"]
        validator.scene_objects = set(scenario["objects"])
        for baseline in BASELINES:
            entry = per_baseline[baseline]
            state = state_from_facts(
                facts=entry["observed_state_facts"], objects=scenario["objects"]
            )
            goal = GoalSpec.model_validate(scenario["goal"])
            holding = parse_holding(entry["observed_state_facts"])
            before = plan_evaluation(
                entry["actions"], validator=validator, state=state, goal=goal
            )
            rewritten, inserted = insert_releases(entry["actions"], holding)
            after = plan_evaluation(
                rewritten, validator=validator, state=state, goal=goal
            )
            cases.append(
                {
                    "record_type": "release_counterfactual_case",
                    "schema_version": args.schema_version,
                    "baseline": baseline,
                    "point_key": [source_task_id, seed, perturbation],
                    "source_task_id": source_task_id,
                    "seed": seed,
                    "perturbation_type": perturbation,
                    "task_family": scenario["task_family"],
                    "observed_holding": holding,
                    "release_inserted": bool(inserted),
                    "inserted_at": [item["position"] for item in inserted],
                    "inserted_objects": [item["object_id"] for item in inserted],
                    "before": before,
                    "after": after,
                    "before_actions": entry["actions"],
                    "after_actions": rewritten,
                    "frozen_recovery_success": entry["frozen_recovery_success"],
                    "frozen_error_code": entry["frozen_error_code"],
                    "frozen_error_layer": entry["frozen_error_layer"],
                }
            )
    return cases, summarize(cases)


def score(case: dict[str, Any], stage: str) -> bool:
    cell = case[stage]
    return bool(cell["valid"] and cell["goal_satisfied"])


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_baseline: dict[str, Any] = {}
    for baseline in BASELINES:
        rows = [c for c in cases if c["baseline"] == baseline]
        before_ok = [score(c, "before") for c in rows]
        after_ok = [score(c, "after") for c in rows]
        fixed = [c for c, b, a in zip(rows, before_ok, after_ok) if a and not b]
        broke = [c for c, b, a in zip(rows, before_ok, after_ok) if b and not a]
        inserted = [c for c in rows if c["release_inserted"]]
        still_failing = [c for c in rows if not score(c, "after")]
        by_baseline[baseline] = {
            "points": len(rows),
            "before_success": sum(before_ok),
            "before_rate": sum(before_ok) / len(rows),
            "after_success": sum(after_ok),
            "after_rate": sum(after_ok) / len(rows),
            "releases_inserted_points": len(inserted),
            "mean_inserted_actions": (
                sum(len(c["inserted_objects"]) for c in rows) / len(rows)
            ),
            "mean_actions_before": sum(c["before"]["action_count"] for c in rows) / len(rows),
            "mean_actions_after": sum(c["after"]["action_count"] for c in rows) / len(rows),
            "paired_fixed": len(fixed),
            "paired_broke": len(broke),
            "fixed_by_perturbation": dict(
                Counter(c["perturbation_type"] for c in fixed).most_common()
            ),
            "broke_by_perturbation": dict(
                Counter(c["perturbation_type"] for c in broke).most_common()
            ),
            "exact_mcnemar_before_vs_after": mcnemar_exact_p(len(fixed), len(broke)),
            "shape_ok_before": sum(c["before"]["shape_ok"] for c in rows),
            "shape_ok_after": sum(c["after"]["shape_ok"] for c in rows),
            "recovered_but_shape_rejected": sum(
                1 for c in fixed if not c["after"]["shape_ok"]
            ),
            "residual_error_codes": dict(
                Counter(
                    str(c["after"].get("error_code") or "NONE") for c in still_failing
                ).most_common()
            ),
            "residual_by_perturbation": dict(
                Counter(c["perturbation_type"] for c in still_failing).most_common()
            ),
            "scorer_matches_frozen_success": sum(
                1 for c in rows if score(c, "before") == c["frozen_recovery_success"]
            ),
        }
    agreement = sum(
        1
        for c in cases
        if score(c, "before") == c["frozen_recovery_success"]
    )
    return {
        "record_type": "release_counterfactual_summary",
        "points": len(cases),
        "by_baseline": by_baseline,
        "scorer_vs_frozen_success_agreement": {
            "cases": len(cases),
            "agree": agreement,
            "rate": agreement / len(cases) if cases else None,
            "disagreements": [
                {
                    "baseline": c["baseline"],
                    "point_key": c["point_key"],
                    "mine": score(c, "before"),
                    "frozen": c["frozen_recovery_success"],
                    "frozen_error_code": c["frozen_error_code"],
                }
                for c in cases
                if score(c, "before") != c["frozen_recovery_success"]
            ][:40],
        },
    }


def render(summary: dict[str, Any]) -> str:
    lines = [
        "# P2 反事实：强制释放前置条件（零 token）",
        "",
        f"规则：观测态说某手臂持着 X，而计划在该手臂上的下一个动作不是 `place(X, ...)`，"
        f"就在它前面插入 `place({RELEASE_TARGET} 方向) 释放 X`。"
        f"评分器是冻结的四层 Validator ＋ Goal Checker（与 428 头条同口径）。",
        "",
        f"重打分器与冻结 `recovery_success` 的一致性："
        f"{summary['scorer_vs_frozen_success_agreement']['agree']}/"
        f"{summary['scorer_vs_frozen_success_agreement']['cases']} = "
        f"{summary['scorer_vs_frozen_success_agreement']['rate']:.4f}"
        f"（不一致清单在 summary.json 里，必须先看这一行再看下表）",
        "",
        "| 臂 | 点数 | before | after | 插入释放的点 | 修好 | 改坏 | 精确 McNemar p | 均值动作 before→after | 冻结形态可执行 before→after | 修好但形态被拒 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|",
    ]
    for baseline, cell in summary["by_baseline"].items():
        lines.append(
            f"| {baseline} | {cell['points']} | {cell['before_success']} "
            f"({cell['before_rate']:.4f}) | {cell['after_success']} "
            f"({cell['after_rate']:.4f}) | {cell['releases_inserted_points']} | "
            f"{cell['paired_fixed']} | {cell['paired_broke']} | "
            f"{cell['exact_mcnemar_before_vs_after']:.4g} | "
            f"{cell['mean_actions_before']:.3f}→{cell['mean_actions_after']:.3f} | "
            f"{cell['shape_ok_before']}→{cell['shape_ok_after']} | "
            f"{cell['recovered_but_shape_rejected']} |"
        )
    lines += ["", "## 残余失败", ""]
    for baseline, cell in summary["by_baseline"].items():
        lines.append(
            f"- `{baseline}`：残余 {cell['points'] - cell['after_success']} 点，"
            f"按扰动 `{json.dumps(cell['residual_by_perturbation'], ensure_ascii=False)}`，"
            f"首错码 `{json.dumps(cell['residual_error_codes'], ensure_ascii=False)}`"
        )
    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal", default="data/collections/state_recovery_formal_v1_20260914_101157.jsonl"
    )
    parser.add_argument("--scenarios", default="data/scenarios/state_recovery_tasks_v1.jsonl")
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--schema-version", default="2026-09-26-release-counterfactual-v1")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    cases, summary = run(args)
    summary["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["inputs"] = {
        "formal": args.formal,
        "scenarios": args.scenarios,
        "registry": args.registry,
    }
    write_jsonl(output_dir / "release_counterfactual_cases.jsonl", cases)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "analysis.md").write_text(render(summary), encoding="utf-8")
    print(json.dumps(summary["by_baseline"], ensure_ascii=False, indent=2))
    print(
        json.dumps(
            summary["scorer_vs_frozen_success_agreement"], ensure_ascii=False, indent=2
        )
    )
    print(json.dumps({"output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
