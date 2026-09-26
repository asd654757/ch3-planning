#!/usr/bin/env python3
"""Natural state-error arm of the frozen State-Recovery protocol (reviewer C2 / P3).

P1 showed that with a trusted observed state a depth-8 symbolic planner reaches
428/428, so the LLM contributes nothing *as a planner* on this protocol.  This
script removes the trusted observation and replaces it with the belief any
action-executing controller actually holds: replay the commanded prefix that was
executed, from the scenario's initial state, through the same frozen simulator
the validator uses.  No injected corruption, no model calls, no tokens -- both
sides come from frozen data.

The difference between that expectation and the recorded observation *is* the
perturbation the benchmark was built around: the command ran, its reported
effect did not happen.  A belief produced by the trusted action semantics cannot
be flagged by any consistency check over (history, belief), so this is the part
of the gap that symbolic machinery cannot close by construction.

Each produced plan is scored three ways:

- ``belief``: validator + goal on the expected state -- what the stack self-reports.
- ``truth``: frozen scoring on the observed state -- the benchmark's own verdict.
- ``truth_goal_physical``: goal facts after simulating the plan on the observed
  state up to the first genuine state-layer failure, i.e. what the world would
  actually look like, without the completeness rule mixed in.

``silent_failure`` is belief-success without the physical goal: the system reports
a completed repair while the world has not reached the goal.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlanAction
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.state_validator import simulate_plan
from scripts.state_recovery_offline_analysis import (
    mcnemar_exact_p,
    read_jsonl,
    state_from_facts,
    write_json,
    write_jsonl,
)
from scripts.symbolic_planner_baseline import (
    ARMS,
    BFS_VALID,
    allowed_skills_for,
    evaluate_suffix,
    search_suffix,
)

from scripts.corrupted_state_recovery_pilot import physical, score

RECORD = "expected_state_case"
SCHEMA = "2026-09-26-expected-state-v1"
PREFIX_STEPS = {"none": 0, "first": 1}


def initial_state(scenario: dict[str, Any]) -> WorldState:
    objects = set(scenario["objects"])
    state = WorldState.table_scene(objects)
    state.at = {**state.at, **scenario["initial_state"].get("at", {})}
    state.holding = dict(scenario["initial_state"].get("holding", {}))
    for obj in list(state.at):
        if obj in set(state.holding.values()):
            del state.at[obj]
    return state


def build_expected(
    *,
    scenario: dict[str, Any],
    actions: list[ModelPlanAction],
    executed_prefix: list[int],
    rule: str,
    valid_targets: set[str],
) -> tuple[WorldState, list[int], Optional[str]]:
    """Replay the executed prefix; return (expected state, used step ids, first error)."""
    if executed_prefix:
        wanted = set(executed_prefix)
        prefix = [action for action in actions if action.step_id in wanted]
    else:
        count = PREFIX_STEPS.get(rule, len(actions))
        prefix = actions[:count]
    state = initial_state(scenario)
    for action in prefix:
        nxt, ok, code, _msg = step(state, action, valid_targets=valid_targets)
        if not ok:
            return state, [a.step_id for a in prefix if a.step_id < action.step_id], (
                code.value if code else "step_failed"
            )
        state = nxt
    return state, [a.step_id for a in prefix], None


def fact_diff(*, expected: WorldState, observed: WorldState) -> dict[str, list[str]]:
    exp = expected.facts() | expected.empty_hand_facts(ARMS)
    obs = observed.facts() | observed.empty_hand_facts(ARMS)
    return {
        "expected_only": sorted(exp - obs),
        "observed_only": sorted(obs - exp),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(flag: str, subset: list[dict[str, Any]]) -> Optional[float]:
        return sum(bool(row[flag]) for row in subset) / len(subset) if subset else None

    control = {row["point_key"]: row for row in rows}
    out: dict[str, Any] = {}
    for perturbation in sorted({row["perturbation_type"] for row in rows}):
        sel = [row for row in rows if row["perturbation_type"] == perturbation]
        out[perturbation] = {
            "points": len(sel),
            "belief_expected_equals_observed": sum(
                1 for row in sel if row["belief_matches_observation"]
            ),
            "search_success_rate": rate("search_success", sel),
            "mean_plan_actions": statistics.mean(row["plan_actions"] for row in sel),
            "belief_success_rate": rate("belief_success", sel),
            "truth_success_rate": rate("truth_success", sel),
            "truth_goal_physical_rate": rate("truth_goal_physical", sel),
            "silent_failure_rate": rate("silent_failure", sel),
            "rejected_in_truth_rate": rate("rejected_in_truth", sel),
            "planner_on_observed_state_rate": rate("observed_state_success", sel),
            "truth_error_codes": dict(
                Counter(
                    row["truth_error_code"] for row in sel if row["truth_error_code"]
                ).most_common()
            ),
            "truth_state_errors": dict(
                Counter(
                    row["truth_state_error"] for row in sel if row["truth_state_error"]
                ).most_common()
            ),
        }
    only_belief = sum(
        1
        for row in rows
        if row["truth_success"] and not row["observed_state_success"]
    )
    only_observed = sum(
        1
        for row in rows
        if row["observed_state_success"] and not row["truth_success"]
    )
    out["ALL"] = {
        "points": len(rows),
        "belief_success_rate": rate("belief_success", rows),
        "truth_success_rate": rate("truth_success", rows),
        "truth_goal_physical_rate": rate("truth_goal_physical", rows),
        "silent_failure_rate": rate("silent_failure", rows),
        "planner_on_observed_state_rate": rate("observed_state_success", rows),
        "mean_plan_actions": statistics.mean(row["plan_actions"] for row in rows),
        "mean_observed_state_actions": statistics.mean(
            row["observed_state_plan_actions"] for row in rows
        ),
        "paired_observed_vs_expected": {
            "only_observed_state_success": only_observed,
            "only_expected_state_success": only_belief,
            "mcnemar_exact_p_value": mcnemar_exact_p(only_observed, only_belief),
        },
    }
    return out


def render(summary: dict[str, Any]) -> str:
    def fmt(value: Optional[float]) -> str:
        return "-" if value is None else f"{value:.4f}"

    lines = [
        "# 自然状态错误：动作历史预期 vs 实际观测（C2 / P3）",
        "",
        "- belief：把**已执行前缀**在冻结模拟器里重放得到的状态当作输入（控制器真实持有的信念）",
        "- truth：冻结记录里的**实际观测状态**（基准自己的判分口径）",
        "- truth goal (phys)：在真实观测状态上推进计划到首个 state 层错误后，目标是否成立",
        "- silent failure：系统自报成功，而真实世界没达成目标",
        "- planner on observed state：同一个规划器改喂实际观测状态（= P1 的 `BFS_VALID` 口径）",
        "- 无任何扰动注入、无模型调用、无 token；两侧状态全部来自冻结数据",
        "",
        "| Perturbation | Points | belief==obs | belief success | truth success (frozen) | truth goal (phys) | Silent failure | Rejected in truth | planner on observed | Mean actions |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cell in summary["by_perturbation"].items():
        if name == "ALL":
            continue
        lines.append(
            f"| {name} | {cell['points']} | "
            f"{cell['belief_expected_equals_observed']}/{cell['points']} | "
            f"{fmt(cell['belief_success_rate'])} | {fmt(cell['truth_success_rate'])} | "
            f"{fmt(cell['truth_goal_physical_rate'])} | {fmt(cell['silent_failure_rate'])} | "
            f"{fmt(cell['rejected_in_truth_rate'])} | "
            f"{fmt(cell['planner_on_observed_state_rate'])} | "
            f"{fmt(cell['mean_plan_actions'])} |"
        )
    all_cell = summary["by_perturbation"]["ALL"]
    paired = all_cell["paired_observed_vs_expected"]
    lines += [
        f"| **ALL** | {all_cell['points']} | - | {fmt(all_cell['belief_success_rate'])} | "
        f"{fmt(all_cell['truth_success_rate'])} | {fmt(all_cell['truth_goal_physical_rate'])} | "
        f"{fmt(all_cell['silent_failure_rate'])} | - | "
        f"{fmt(all_cell['planner_on_observed_state_rate'])} | "
        f"{fmt(all_cell['mean_plan_actions'])} |",
        "",
        f"全量配对（喂观测状态 vs 喂预期状态，冻结口径 success）："
        f"only_observed={paired['only_observed_state_success']}, "
        f"only_expected={paired['only_expected_state_success']}, "
        f"精确 McNemar p={fmt(paired['mcnemar_exact_p_value'])}",
        "",
        "## 各扰动的真实侧首错码",
        "",
    ]
    for name, cell in summary["by_perturbation"].items():
        if name == "ALL":
            continue
        lines.append(
            f"- `{name}`：冻结口径 `{json.dumps(cell['truth_error_codes'], ensure_ascii=False)}`；"
            f"真实 state 层 `{json.dumps(cell['truth_state_errors'], ensure_ascii=False)}`"
        )
    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal",
        default="data/collections/state_recovery_formal_v1_20260914_101157.jsonl",
    )
    parser.add_argument(
        "--scenarios", default="data/scenarios/state_recovery_tasks_v1.jsonl"
    )
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-actions", type=int, default=8)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_dir}.")
    output_dir.mkdir(parents=True, exist_ok=False)

    scenarios = {row["task_id"]: row for row in read_jsonl(Path(args.scenarios))}
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)

    points: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in read_jsonl(Path(args.formal)):
        if row.get("record_type") != "state_recovery":
            continue
        points.setdefault(
            (str(row["source_task_id"]), int(row["seed"]), str(row["perturbation_type"])),
            row,
        )
    selected = sorted(points.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))
    if args.limit:
        selected = selected[: args.limit]

    rows: list[dict[str, Any]] = []
    started = time.time()
    for (source_task_id, seed, perturbation), record in selected:
        scenario = scenarios[f"{source_task_id}__{perturbation}"]
        goal = GoalSpec.model_validate(scenario["goal"])
        skills = allowed_skills_for(scenario)
        objects = set(scenario["objects"])
        validator.scene_objects = set(objects)
        valid_targets = validator.valid_targets

        observed_facts = set(record["observed_state_facts"])
        observed_state = state_from_facts(facts=observed_facts, objects=objects)
        plan_actions = [
            ModelPlanAction.model_validate(item)
            for item in record["model_plan"]["actions"]
        ]
        expected_state, used_prefix, replay_error = build_expected(
            scenario=scenario,
            actions=plan_actions,
            executed_prefix=list(record.get("executed_prefix_step_ids") or []),
            rule=str(scenario.get("prefix_length_rule", "full")),
            valid_targets=valid_targets,
        )

        # The planner under test, run twice: once on the belief it is handed,
        # once on the observation (the P1 ``BFS_VALID`` reference arm).
        results: dict[str, dict[str, Any]] = {}
        for tag, state in (("belief", expected_state), ("observed", observed_state)):
            found, suffix, reason = search_suffix(
                state=state,
                goal=goal,
                valid_targets=valid_targets,
                allowed_skills=skills,
                mode=BFS_VALID,
                max_actions=args.max_actions,
            )
            actions = [ModelPlanAction.model_validate(item) for item in suffix]
            own = score(actions=actions, validator=validator, state=state, goal=goal)
            phys = physical(
                actions=actions, state=observed_state, goal=goal, valid_targets=valid_targets
            )
            results[tag] = {
                "search_success": bool(found),
                "search_reason": reason,
                "plan_actions": len(actions),
                "plan": [action.model_dump() for action in actions],
                "belief_valid": own["valid"],
                "belief_goal": own["goal_satisfied"],
                "belief_success": bool(own["valid"] and own["goal_satisfied"]),
                "truth_valid": None,
                "truth_goal": None,
                "truth_success": None,
                "truth_error_code": None,
                "truth_state_error": None,
                "truth_goal_physical": phys["goal_satisfied"],
                "silent_failure": bool(
                    own["valid"] and own["goal_satisfied"] and not phys["goal_satisfied"]
                ),
                "rejected_in_truth": False,
            }
            if tag == "belief":
                truth = score(
                    actions=actions, validator=validator, state=observed_state, goal=goal
                )
                results[tag].update(
                    truth_valid=truth["valid"],
                    truth_goal=truth["goal_satisfied"],
                    truth_success=bool(truth["valid"] and truth["goal_satisfied"]),
                    truth_error_code=truth["error_code"],
                    truth_state_error=phys["first_state_error"],
                    rejected_in_truth=bool(
                        own["valid"] and own["goal_satisfied"] and not truth["valid"]
                    ),
                )

        diff = fact_diff(expected=expected_state, observed=observed_state)
        rows.append(
            {
                "record_type": RECORD,
                "schema_version": SCHEMA,
                "point_key": f"{source_task_id}|{seed}|{perturbation}",
                "source_task_id": source_task_id,
                "seed": seed,
                "perturbation_type": perturbation,
                "task_family": scenario["task_family"],
                "prefix_length_rule": scenario.get("prefix_length_rule"),
                "executed_prefix_step_ids": list(record.get("executed_prefix_step_ids") or []),
                "replayed_prefix_step_ids": used_prefix,
                "prefix_replay_error": replay_error,
                "belief_matches_observation": not (
                    diff["expected_only"] or diff["observed_only"]
                ),
                "fact_diff": diff,
                "plan_actions": results["belief"]["plan_actions"],
                "plan": results["belief"]["plan"],
                "search_success": results["belief"]["search_success"],
                "search_reason": results["belief"]["search_reason"],
                "belief_valid": results["belief"]["belief_valid"],
                "belief_goal": results["belief"]["belief_goal"],
                "belief_success": results["belief"]["belief_success"],
                "truth_valid": results["belief"]["truth_valid"],
                "truth_goal": results["belief"]["truth_goal"],
                "truth_success": results["belief"]["truth_success"],
                "truth_error_code": results["belief"]["truth_error_code"],
                "truth_state_error": results["belief"]["truth_state_error"],
                "truth_goal_physical": results["belief"]["truth_goal_physical"],
                "silent_failure": results["belief"]["silent_failure"],
                "rejected_in_truth": results["belief"]["rejected_in_truth"],
                "observed_state_success": results["observed"]["belief_success"],
                "observed_state_plan_actions": results["observed"]["plan_actions"],
                "observed_state_plan": results["observed"]["plan"],
                "expected_facts": sorted(
                    expected_state.facts() | expected_state.empty_hand_facts(ARMS)
                ),
                "observed_facts": sorted(
                    observed_state.facts() | observed_state.empty_hand_facts(ARMS)
                ),
            }
        )

    summary = {
        "record_type": "expected_state_summary",
        "schema_version": SCHEMA,
        "protocol": "state_recovery_428",
        "planner_mode": BFS_VALID,
        "points": len(selected),
        "rows": len(rows),
        "max_actions": args.max_actions,
        "prefix_replay_errors": sum(
            1 for row in rows if row["prefix_replay_error"] is not None
        ),
        "by_perturbation": summarize(rows),
        "elapsed_s": time.time() - started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal": args.formal,
        "scenarios": args.scenarios,
    }
    write_jsonl(output_dir / f"{RECORD}s.jsonl", rows)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "analysis.md").write_text(render(summary), encoding="utf-8")
    all_cell = summary["by_perturbation"]["ALL"]
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "points": summary["points"],
                "elapsed_s": round(summary["elapsed_s"], 2),
                "prefix_replay_errors": summary["prefix_replay_errors"],
                "belief_success": all_cell["belief_success_rate"],
                "truth_success_frozen": all_cell["truth_success_rate"],
                "truth_goal_physical": all_cell["truth_goal_physical_rate"],
                "silent_failure": all_cell["silent_failure_rate"],
                "planner_on_observed_state": all_cell["planner_on_observed_state_rate"],
                "by_perturbation_silent_failure": {
                    key: value["silent_failure_rate"]
                    for key, value in summary["by_perturbation"].items()
                    if key != "ALL"
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
