#!/usr/bin/env python3
"""Offline P0/P1 analyses for the frozen State Recovery-428 benchmark.

This script performs three zero-VLM analyses:

1. Recovery Feasibility Certificate: BFS over the discrete symbolic action
   model to determine whether a legal recovery path exists for each point.
2. Goal Checker ablation: replay ROUTED w/o Goal Checker by accepting the R2
   candidate whenever the Validator passes, without goal-triggered fallback.
3. Action overhead: compare each repaired suffix with the shortest symbolic
   oracle path found by the feasibility search.

The input data and prompts are frozen; no VLM is called.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlanAction, Skill
from ch3.state.world_state import WorldState


ARMS = ("left", "right")
OVERHEAD_ARMS = ("R2_STATE", "R1_FROM_STATE", "ROUTED")
SKILL_ORDER = ("pick", "place", "push", "press")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_fact(fact: str) -> tuple[str, tuple[str, ...]]:
    if not fact.endswith(")"):
        raise ValueError(f"unsupported fact: {fact}")
    predicate = fact[: fact.index("(")].strip()
    inner = fact[fact.index("(") + 1 : -1]
    args = tuple(part.strip() for part in inner.split(",")) if inner else ()
    return predicate, args


def state_from_facts(
    *,
    facts: Iterable[str],
    objects: Iterable[str],
    table_id: str = "table",
) -> WorldState:
    """Reconstruct the observed discrete state from frozen fact strings."""
    object_set = set(objects)
    state = WorldState.table_scene(object_set)
    state.table_id = table_id
    for fact in facts:
        if not isinstance(fact, str):
            continue
        try:
            predicate, args = parse_fact(fact)
        except ValueError:
            continue
        if predicate == "holding" and len(args) == 2:
            arm, obj = args
            if obj in object_set:
                state.holding[arm] = obj
        elif predicate == "on" and len(args) == 2:
            obj, loc = args
            if obj in object_set:
                state.at[obj] = loc
        elif predicate == "pushed_to" and len(args) == 2:
            obj, loc = args
            if obj in object_set:
                state.at[obj] = loc
                state.pushed.add(obj)
        elif predicate == "pressed" and len(args) == 1:
            if args[0] in object_set:
                state.pressed.add(args[0])
    return state


def state_key(state: WorldState) -> tuple[Any, ...]:
    return (
        tuple(sorted(state.holding.items())),
        tuple(sorted(state.at.items())),
        tuple(sorted(state.pushed)),
        tuple(sorted(state.pressed)),
        state.table_id,
    )


def candidate_actions(
    state: WorldState,
    *,
    allowed_skills: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Generate all registered symbolic actions in a deterministic order."""
    actions: list[dict[str, Any]] = []
    targets = sorted(state.objects | {state.table_id})
    skill_allowed = allowed_skills or set(SKILL_ORDER)

    for arm in ARMS:
        held = state.holding.get(arm)
        if held is not None:
            for target in targets:
                if target in state.holding.values():
                    continue
                if "place" in skill_allowed:
                    actions.append(
                        {
                            "skill": "place",
                            "object_id": held,
                            "target_id": target,
                            "arm": arm,
                        }
                    )
        else:
            for obj in sorted(state.objects):
                if state.is_on_table(obj):
                    if "pick" in skill_allowed:
                        actions.append(
                            {
                                "skill": "pick",
                                "object_id": obj,
                                "target_id": None,
                                "arm": arm,
                            }
                        )

    for arm in ARMS:
        if state.arm_empty(arm):
            for obj in sorted(state.objects):
                if not state.is_on_table(obj):
                    continue
                for target in targets:
                    if target in state.holding.values():
                        continue
                    if "push" in skill_allowed:
                        actions.append(
                            {
                                "skill": "push",
                                "object_id": obj,
                                "target_id": target,
                                "arm": arm,
                            }
                        )
            for obj in sorted(state.objects):
                if "press" in skill_allowed:
                    actions.append(
                        {
                            "skill": "press",
                            "object_id": obj,
                            "target_id": None,
                            "arm": arm,
                        }
                    )
    return actions


def apply_symbolic_action(
    state: WorldState, action: Mapping[str, Any]
) -> tuple[WorldState, bool]:
    """Apply a generated action using the frozen symbolic transition model."""
    try:
        plan_action = ModelPlanAction.model_validate(
            {**action, "step_id": 1}
        )
    except Exception:
        return state.copy(), False

    from ch3.state.simulator import step

    next_state, ok, _code, _message = step(
        state, plan_action, valid_targets=state.objects | {state.table_id}
    )
    return next_state, ok


def shortest_recovery(
    *,
    state: WorldState,
    goal_facts: Iterable[str],
    arms: Iterable[str] = ARMS,
    max_depth: int = 8,
    allowed_skills: set[str] | None = None,
) -> tuple[bool, list[dict[str, Any]], str]:
    """Find the shortest legal recovery plan with breadth-first search."""
    goal = GoalSpec.model_validate({"facts": list(goal_facts)})
    arm_set = set(arms)
    initial_plan: list[dict[str, Any]] = []
    if goal_satisfied(state, goal, arm_set):
        return True, initial_plan, "goal_already_satisfied"

    start_key = state_key(state)
    queue: deque[tuple[WorldState, list[dict[str, Any]]]] = deque()
    queue.append((state.copy(), initial_plan))
    visited: dict[tuple[Any, ...], int] = {start_key: 0}

    while queue:
        current, plan = queue.popleft()
        if len(plan) >= max_depth:
            continue
        for action in candidate_actions(current, allowed_skills=allowed_skills):
            nxt, ok = apply_symbolic_action(current, action)
            if not ok:
                continue
            candidate = [
                {**item, "step_id": index + 1}
                for index, item in enumerate([*plan, action])
            ]
            if goal_satisfied(nxt, goal, arm_set):
                return True, candidate, "bfs_success"
            nxt_key = state_key(nxt)
            depth = len(candidate)
            if nxt_key not in visited or visited[nxt_key] > depth:
                visited[nxt_key] = depth
                queue.append((nxt, candidate))
    return False, [], "search_limit_exceeded"


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value for discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1))
    return min(1.0, 2.0 * tail * (0.5**n))


def mean_or_none(values: list[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def median_or_none(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def feasibility_certificate(
    *,
    formal_rows: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    max_depth: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scenarios_by_id = {row["task_id"]: row for row in scenarios}
    formal_points: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in formal_rows:
        key = (
            str(row["source_task_id"]),
            int(row["seed"]),
            str(row["perturbation_type"]),
        )
        formal_points.setdefault(key, row)

    missing_scenarios: list[str] = []
    certificates: list[dict[str, Any]] = []
    by_perturbation: dict[str, Counter[str]] = defaultdict(Counter)
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    oracle_lengths: dict[str, list[int]] = defaultdict(list)

    for (source_task_id, seed, perturbation), _row in sorted(
        formal_points.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])
    ):
        scenario = scenarios_by_id.get(f"{source_task_id}__{perturbation}")
        if scenario is None:
            missing_scenarios.append(f"{source_task_id}__{perturbation}")
            continue
        state = state_from_facts(
            facts=formal_points[(source_task_id, seed, perturbation)][
                "observed_state_facts"
            ],
            objects=scenario["objects"],
        )
        allowed_skills = set(
            scenario.get("hard_factors", {}).get("required_skills", [])
        ) | {"place"}
        recoverable, oracle_plan, reason = shortest_recovery(
            state=state,
            goal_facts=scenario["goal"]["facts"],
            max_depth=max_depth,
            allowed_skills=allowed_skills,
        )
        family = str(scenario["task_family"])
        result = {
            "record_type": "state_recovery_feasibility_certificate",
            "schema_version": "state-recovery-feasibility-v1",
            "source_task_id": source_task_id,
            "seed": seed,
            "perturbation_type": perturbation,
            "task_family": family,
            "objects": sorted(scenario["objects"]),
            "allowed_skills": sorted(allowed_skills),
            "goal_facts": list(scenario["goal"]["facts"]),
            "observed_state_facts": list(
                state.facts() | state.empty_hand_facts(ARMS)
            ),
            "recoverable": recoverable,
            "search_reason": reason,
            "oracle_length": len(oracle_plan),
            "oracle_plan": oracle_plan,
            "search_max_depth": max_depth,
        }
        certificates.append(result)
        status = "recoverable" if recoverable else "infeasible"
        by_perturbation[perturbation][f"{status}_points"] += 1
        by_family[family][f"{status}_points"] += 1
        oracle_lengths[perturbation].append(len(oracle_plan))

    total = len(certificates)
    recoverable = sum(row["recoverable"] for row in certificates)
    summary: dict[str, Any] = {
        "record_type": "state_recovery_feasibility_summary",
        "schema_version": "state-recovery-feasibility-v1",
        "formal_points": total,
        "recoverable_points": recoverable,
        "infeasible_points": total - recoverable,
        "recoverable_rate": recoverable / total if total else None,
        "search_max_depth": max_depth,
        "missing_scenarios": sorted(missing_scenarios),
        "by_perturbation": {},
        "by_task_family": {},
    }
    for perturbation, counts in sorted(by_perturbation.items()):
        n = counts["recoverable_points"] + counts["infeasible_points"]
        lengths = oracle_lengths[perturbation]
        summary["by_perturbation"][perturbation] = {
            "points": n,
            "recoverable_points": counts["recoverable_points"],
            "infeasible_points": counts["infeasible_points"],
            "recoverable_rate": counts["recoverable_points"] / n if n else None,
            "mean_oracle_length": mean_or_none([float(x) for x in lengths]),
            "median_oracle_length": median_or_none([float(x) for x in lengths]),
            "max_oracle_length": max(lengths) if lengths else None,
        }
    for family, counts in sorted(by_family.items()):
        n = counts["recoverable_points"] + counts["infeasible_points"]
        summary["by_task_family"][family] = {
            "points": n,
            "recoverable_points": counts["recoverable_points"],
            "infeasible_points": counts["infeasible_points"],
            "recoverable_rate": counts["recoverable_points"] / n if n else None,
        }
    return certificates, summary


def goal_checker_ablation(
    formal_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    routed_rows = [row for row in formal_rows if row["baseline"] == "ROUTED"]
    records: list[dict[str, Any]] = []
    paired = Counter()
    by_perturbation: dict[str, Counter[str]] = defaultdict(Counter)

    for row in sorted(
        routed_rows,
        key=lambda item: (
            item["source_task_id"],
            int(item["seed"]),
            item["perturbation_type"],
        ),
    ):
        r2_valid = bool(row.get("r2_valid"))
        r2_goal_ok = bool(row.get("r2_goal_ok"))
        without_gc_success = r2_valid and r2_goal_ok
        without_gc_pbw = r2_valid and not r2_goal_ok
        full_success = bool(row.get("recovery_success"))
        full_pbw = bool(row.get("pass_but_wrong"))
        record = {
            "record_type": "state_recovery_goal_checker_ablation_point",
            "schema_version": "state-recovery-gc-ablation-v1",
            "source_task_id": row["source_task_id"],
            "task_id": row["task_id"],
            "seed": int(row["seed"]),
            "perturbation_type": row["perturbation_type"],
            "r2_valid": r2_valid,
            "r2_goal_ok": r2_goal_ok,
            "full_routed_success": full_success,
            "full_routed_pbw": full_pbw,
            "full_routed_fallback_triggered": bool(
                row.get("fallback_triggered")
            ),
            "routed_without_goal_checker_success": without_gc_success,
            "routed_without_goal_checker_pbw": without_gc_pbw,
            "routed_without_goal_checker_fallback": False,
        }
        records.append(record)

        perturbation = str(row["perturbation_type"])
        counts = by_perturbation[perturbation]
        counts["ROUTED_without_GC_points"] += 1
        counts["ROUTED_without_GC_success"] += int(without_gc_success)
        counts["ROUTED_without_GC_pbw"] += int(without_gc_pbw)
        counts["ROUTED_points"] += 1
        counts["ROUTED_success"] += int(full_success)
        counts["ROUTED_pbw"] += int(full_pbw)
        counts["ROUTED_fallback"] += int(bool(row.get("fallback_triggered")))

        if without_gc_success and full_success:
            paired["both_success"] += 1
        elif without_gc_success and not full_success:
            paired["without_gc_only_success"] += 1
        elif not without_gc_success and full_success:
            paired["full_only_success"] += 1
        else:
            paired["both_fail"] += 1

    total = len(records)
    wo_success = sum(row["routed_without_goal_checker_success"] for row in records)
    full_success = sum(row["full_routed_success"] for row in records)
    b = paired["without_gc_only_success"]
    c = paired["full_only_success"]
    summary = {
        "record_type": "state_recovery_goal_checker_ablation_summary",
        "schema_version": "state-recovery-gc-ablation-v1",
        "points": total,
        "ROUTED_without_GC": {
            "points": total,
            "success": wo_success,
            "success_rate": wo_success / total if total else None,
            "pbw": sum(row["routed_without_goal_checker_pbw"] for row in records),
            "fallback": 0,
        },
        "ROUTED_full": {
            "points": total,
            "success": full_success,
            "success_rate": full_success / total if total else None,
            "pbw": sum(row["full_routed_pbw"] for row in records),
            "fallback": sum(row["full_routed_fallback_triggered"] for row in records),
        },
        "paired_success_counts": dict(paired),
        "mcnemar_full_vs_without_gc": {
            "only_full_success": c,
            "only_without_gc_success": b,
            "discordant": b + c,
            "p_value": mcnemar_exact_p(b, c),
        },
        "by_perturbation": {},
    }
    for perturbation, counts in sorted(by_perturbation.items()):
        n = counts["ROUTED_without_GC_points"]
        summary["by_perturbation"][perturbation] = {
            "ROUTED_without_GC": {
                "points": n,
                "success": counts["ROUTED_without_GC_success"],
                "success_rate": counts["ROUTED_without_GC_success"] / n if n else None,
                "pbw": counts["ROUTED_without_GC_pbw"],
                "fallback": counts["ROUTED_without_GC_fallback"],
            },
            "ROUTED_full": {
                "points": counts["ROUTED_points"],
                "success": counts["ROUTED_success"],
                "success_rate": counts["ROUTED_success"] / n if n else None,
                "pbw": counts["ROUTED_pbw"],
                "fallback": counts["ROUTED_fallback"],
            },
        }
    return records, summary


def action_overhead_analysis(
    *,
    formal_rows: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cert_by_point = {
        (row["source_task_id"], int(row["seed"]), row["perturbation_type"]): row
        for row in certificates
    }
    formal_by_point: dict[tuple[str, int, str], dict[str, dict[str, Any]]] = (
        defaultdict(dict)
    )
    for row in formal_rows:
        if row["baseline"] not in OVERHEAD_ARMS:
            continue
        key = (
            str(row["source_task_id"]),
            int(row["seed"]),
            str(row["perturbation_type"]),
        )
        formal_by_point[key][str(row["baseline"])] = row

    records: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    grouped_by_perturbation: dict[str, dict[str, dict[str, list[float]]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    )

    for (source_task_id, seed, perturbation), arms in sorted(
        formal_by_point.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])
    ):
        cert = cert_by_point.get((source_task_id, seed, perturbation))
        if cert is None:
            continue
        for arm in OVERHEAD_ARMS:
            row = arms.get(arm)
            if row is None:
                continue
            repair_length = float(row.get("suffix_action_count", 0) or 0)
            oracle_length = float(cert["oracle_length"])
            overhead = repair_length - oracle_length
            normalized = overhead / max(1.0, oracle_length)
            success = bool(row.get("recovery_success"))
            record = {
                "record_type": "state_recovery_action_overhead_point",
                "schema_version": "state-recovery-action-overhead-v1",
                "source_task_id": source_task_id,
                "seed": seed,
                "perturbation_type": perturbation,
                "method": arm,
                "recovery_success": success,
                "repair_suffix_actions": int(repair_length),
                "oracle_actions": int(oracle_length),
                "action_overhead": int(overhead),
                "normalized_action_overhead": normalized,
                "model_calls": int(row.get("model_calls", 0) or 0),
                "input_tokens": int(row.get("input_tokens", 0) or 0),
                "output_tokens": int(row.get("output_tokens", 0) or 0),
                "total_tokens": int(row.get("total_tokens", 0) or 0),
                "fallback_triggered": bool(row.get("fallback_triggered")),
                "recoverable": bool(cert["recoverable"]),
            }
            records.append(record)
            for source in (grouped[arm], grouped_by_perturbation[perturbation][arm]):
                source["recovery_success"].append(float(success))
                source["repair_suffix_actions"].append(repair_length)
                source["action_overhead"].append(overhead)
                source["normalized_action_overhead"].append(normalized)
                source["model_calls"].append(float(record["model_calls"]))
                source["input_tokens"].append(float(record["input_tokens"]))
                source["output_tokens"].append(float(record["output_tokens"]))
                source["total_tokens"].append(float(record["total_tokens"]))

    def summarize(values: Mapping[str, list[float]]) -> dict[str, Any]:
        n = len(values["recovery_success"])
        return {
            "points": n,
            "recovery_success": int(sum(values["recovery_success"])),
            "recovery_success_rate": (
                sum(values["recovery_success"]) / n if n else None
            ),
            "mean_repair_suffix_actions": mean_or_none(
                values["repair_suffix_actions"]
            ),
            "median_repair_suffix_actions": median_or_none(
                values["repair_suffix_actions"]
            ),
            "mean_action_overhead": mean_or_none(values["action_overhead"]),
            "mean_normalized_action_overhead": mean_or_none(
                values["normalized_action_overhead"]
            ),
            "mean_model_calls": mean_or_none(values["model_calls"]),
            "total_input_tokens": int(sum(values["input_tokens"])),
            "total_output_tokens": int(sum(values["output_tokens"])),
            "total_tokens": int(sum(values["total_tokens"])),
        }

    summary = {
        "record_type": "state_recovery_action_overhead_summary",
        "schema_version": "state-recovery-action-overhead-v1",
        "points": len({(r["source_task_id"], r["seed"], r["perturbation_type"]) for r in records}),
        "by_method": {arm: summarize(grouped[arm]) for arm in OVERHEAD_ARMS},
        "by_perturbation_by_method": {
            perturbation: {
                arm: summarize(values)
                for arm, values in sorted(by_method.items())
            }
            for perturbation, by_method in sorted(
                grouped_by_perturbation.items()
            )
        },
    }
    return records, summary


def render_report(
    *,
    feasibility_summary: Mapping[str, Any],
    gc_summary: Mapping[str, Any],
    overhead_summary: Mapping[str, Any],
) -> str:
    lines = [
        "# State Recovery Offline P0/P1 Analysis",
        "",
        "## 1. Recovery Feasibility Certificate",
        "",
        f"- Formal points: **{feasibility_summary['formal_points']}**",
        f"- Recoverable: **{feasibility_summary['recoverable_points']}**",
        f"- Infeasible: **{feasibility_summary['infeasible_points']}**",
        f"- Recoverable rate: **{feasibility_summary['recoverable_rate']:.4f}**",
        f"- Search depth limit: **{feasibility_summary['search_max_depth']}**",
        "",
        "| Perturbation | Points | Recoverable | Infeasible | Rate | Mean oracle actions |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for perturbation, row in feasibility_summary["by_perturbation"].items():
        lines.append(
            f"| {perturbation} | {row['points']} | {row['recoverable_points']} | "
            f"{row['infeasible_points']} | {row['recoverable_rate']:.4f} | "
            f"{row['mean_oracle_length']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 2. Goal Checker Ablation",
            "",
            "| Method | Points | Success | Success rate | PBW | Fallback |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name in ("ROUTED_without_GC", "ROUTED_full"):
        row = gc_summary[name]
        lines.append(
            f"| {name} | {row['points']} | {row['success']} | "
            f"{row['success_rate']:.4f} | {row['pbw']} | {row['fallback']} |"
        )
    paired = gc_summary["paired_success_counts"]
    lines.extend(
        [
            "",
            "Paired success counts:",
            "",
            "```text",
            f"both_success              = {paired.get('both_success', 0)}",
            f"full_only_success         = {paired.get('full_only_success', 0)}",
            f"without_GC_only_success   = {paired.get('without_gc_only_success', 0)}",
            f"both_fail                 = {paired.get('both_fail', 0)}",
            "",
            f"McNemar exact p-value     = "
            f"{gc_summary['mcnemar_full_vs_without_gc']['p_value']:.8g}",
            "```",
            "",
            "## 3. Action Overhead / Oracle Efficiency",
            "",
            "| Method | Points | RSR | Mean repair actions | Mean overhead | Mean NAO | Mean calls |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for method, row in overhead_summary["by_method"].items():
        lines.append(
            f"| {method} | {row['points']} | {row['recovery_success_rate']:.4f} | "
            f"{row['mean_repair_suffix_actions']:.3f} | "
            f"{row['mean_action_overhead']:.3f} | "
            f"{row['mean_normalized_action_overhead']:.3f} | "
            f"{row['mean_model_calls']:.3f} |"
        )
    lines.extend(
        [
            "",
            "Notes:",
            "",
            "- RSR is Recovery Success Rate on the original 428-point denominator.",
            "- Oracle length is the shortest legal symbolic recovery plan.",
            "- Feasibility search allows the task-family skills, plus `place` as the registered safe-release capability for a held object. It does not allow cross-family skill substitution.",
            "- This report uses zero VLM calls and does not modify the frozen formal dataset.",
            "",
        ]
    )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal",
        default="data/collections/state_recovery_formal_v1_20260914_101157.jsonl",
    )
    parser.add_argument(
        "--scenarios",
        default="data/scenarios/state_recovery_tasks_v1.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="New timestamped output directory; existing files are not overwritten.",
    )
    parser.add_argument("--max-depth", type=int, default=8)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {output_dir}. Use a new timestamp."
        )
    output_dir.mkdir(parents=True, exist_ok=False)

    formal_rows = read_jsonl(Path(args.formal))
    scenarios = read_jsonl(Path(args.scenarios))

    certificates, feasibility_summary = feasibility_certificate(
        formal_rows=formal_rows,
        scenarios=scenarios,
        max_depth=args.max_depth,
    )
    gc_records, gc_summary = goal_checker_ablation(formal_rows=formal_rows)
    overhead_records, overhead_summary = action_overhead_analysis(
        formal_rows=formal_rows, certificates=certificates
    )

    report = render_report(
        feasibility_summary=feasibility_summary,
        gc_summary=gc_summary,
        overhead_summary=overhead_summary,
    )

    write_jsonl(output_dir / "feasibility_certificates.jsonl", certificates)
    write_jsonl(output_dir / "goal_checker_ablation_points.jsonl", gc_records)
    write_jsonl(output_dir / "action_overhead_points.jsonl", overhead_records)
    write_json(output_dir / "feasibility_summary.json", feasibility_summary)
    write_json(output_dir / "goal_checker_ablation_summary.json", gc_summary)
    write_json(output_dir / "action_overhead_summary.json", overhead_summary)
    (output_dir / "analysis.md").write_text(report, encoding="utf-8")

    print(json.dumps({
        "completed_at_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "formal_points": feasibility_summary["formal_points"],
        "recoverable_points": feasibility_summary["recoverable_points"],
        "infeasible_points": feasibility_summary["infeasible_points"],
        "goal_checker_ablation_points": len(gc_records),
        "action_overhead_points": len(overhead_records),
        "output_dir": str(output_dir),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
