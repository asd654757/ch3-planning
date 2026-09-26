#!/usr/bin/env python3
"""State-corruption sweep of the frozen symbolic stack (reviewer item C2 / P3).

The 428 and Fair-100 protocols hand both the planner and the validator a correct
closed-world fact set, which is why the deterministic planner wins there.  This
sweep removes that gift *on the perception side only*: the true scene stays
frozen, but the believed fact set is corrupted, and the planner and the validator
both run on the believed state, as they would in a real deployment.

Every produced plan is scored twice:

- ``belief``: validator pass + goal check on the believed state -- what the stack
  itself would report.
- ``truth``: validator pass + goal check on the true frozen state -- what would
  actually happen if the plan were executed.

``confidently_wrong`` is belief-success without truth-success; that gap is the
only place where a semantic prior can beat an optimal planner, so this measures
whether the gap is big enough to be worth paying for model calls.  Zero VLM
calls, zero tokens, nothing modified.

``none`` is a control and must reproduce the ``BFS_VALID`` 428/428 exactly; it is
an integrity check on this script, not a result.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlan, ModelPlanAction
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
    BFS_VALID,
    allowed_skills_for,
    evaluate_suffix,
    search_suffix,
)


RECORD = "state_corruption_case"
SCHEMA = "2026-09-26-state-corruption-v1"
ARMS = ("left", "right")
TABLE = "table"
PHANTOM = "phantom_object_999"
CORRUPTIONS = (
    "none",
    "holding_hidden",
    "object_loc_wrong",
    "identity_swap",
    "goal_fact_added",
    "phantom_object",
)

OPERATOR_NOTES = {
    "none": "control: believed == true (must reproduce P1 ``BFS_VALID``)",
    "holding_hidden": "the held object is reported as resting on the table, arm empty",
    "object_loc_wrong": "one object's surface is reported as another surface",
    "identity_swap": "two objects' reported surfaces are exchanged",
    "goal_fact_added": "a missing goal fact is reported as already true (illusion of completion)",
    "phantom_object": "an undetected extra object is added to the believed scene",
}


def parse_fact(fact: str) -> tuple[str, list[str]]:
    head, _, tail = fact.partition("(")
    return head.strip(), [item.strip() for item in tail.rstrip(")").split(",")]


def subject_of(fact: str) -> Optional[str]:
    """The object a fact is about (for ``holding``, the held object)."""
    predicate, args = parse_fact(fact)
    if predicate in {"on", "pushed_to"} and len(args) == 2:
        return args[0]
    if predicate == "holding" and len(args) == 2:
        return args[1]
    if predicate == "pressed" and len(args) == 1:
        return args[0]
    return None


def clear_object(facts: set[str], obj: str) -> None:
    """Drop every fact that fixes where ``obj`` is or who holds it."""
    for fact in list(facts):
        if subject_of(fact) != obj:
            continue
        predicate, args = parse_fact(fact)
        facts.discard(fact)
        if predicate == "holding":
            facts.add(f"hand_empty({args[0]})")


def believed_locations(facts: set[str], objects: set[str]) -> dict[str, str]:
    """Believed surface per object; held objects have no location."""
    at = {obj: TABLE for obj in objects}
    for fact in sorted(facts):
        predicate, args = parse_fact(fact)
        if predicate in {"on", "pushed_to"} and len(args) == 2 and args[0] in at:
            at[args[0]] = args[1]
        if predicate == "holding" and len(args) == 2:
            at.pop(args[1], None)
    return at


def was_pushed(facts: set[str], obj: str) -> bool:
    return any(
        predicate == "pushed_to" and len(args) == 2 and args[0] == obj
        for predicate, args in (parse_fact(fact) for fact in facts)
    )


def placed_facts(obj: str, loc: str, *, pushed: bool) -> set[str]:
    """The fact set ``WorldState.facts()`` would emit for ``obj`` at ``loc``."""
    out = {f"on({obj}, {loc})"}
    if pushed:
        out.add(f"pushed_to({obj}, {loc})")
    return out


def assert_consistent(facts: set[str], objects: set[str]) -> None:
    """A believed fact set must not pin one object to two locations.

    ``state_from_facts`` applies ``on``-facts in set-iteration order, so a
    contradictory fact set makes the believed state arbitrary; the sweep would
    then be measuring hash order instead of corruption.
    """
    locations: dict[str, set[str]] = {}
    for fact in sorted(facts):
        predicate, args = parse_fact(fact)
        if predicate not in {"on", "pushed_to"} or len(args) != 2:
            continue
        if args[0] in objects:
            locations.setdefault(args[0], set()).add(args[1])
    for obj, locs in sorted(locations.items()):
        if len(locs) > 1:
            raise AssertionError(
                f"contradictory believed locations for {obj}: {sorted(locs)}"
            )


def apply_corruption(
    corruption: str,
    *,
    facts: set[str],
    objects: set[str],
    goal_facts: set[str],
    rng: random.Random,
) -> tuple[set[str], set[str], str]:
    """Return (believed facts, believed objects, applied|not_applicable)."""
    out = set(facts)
    objs = set(objects)
    holders = sorted(fact for fact in facts if fact.startswith("holding("))

    if corruption == "none":
        return out, objs, "not_applicable"
    if corruption == "holding_hidden":
        if not holders:
            return out, objs, "not_applicable"
        victim = rng.choice(holders)
        _, (arm, obj) = parse_fact(victim)
        out.discard(victim)
        out.update(placed_facts(obj, TABLE, pushed=was_pushed(facts, obj)))
        out.add(f"hand_empty({arm})")
        return out, objs, "applied"
    if corruption == "object_loc_wrong":
        at = believed_locations(out, objs)
        movable = sorted(at)
        if not movable:
            return out, objs, "not_applicable"
        obj = rng.choice(movable)
        current = at[obj]
        candidates = [
            loc
            for loc in sorted(objs | {TABLE})
            if loc != obj and loc != current and at.get(loc) != obj
        ]
        if not candidates:
            return out, objs, "not_applicable"
        new = rng.choice(candidates)
        clear_object(out, obj)
        out.update(placed_facts(obj, new, pushed=was_pushed(facts, obj)))
        return out, objs, "applied"
    if corruption == "identity_swap":
        at = believed_locations(out, objs)
        pairs = [
            (a, b)
            for index, a in enumerate(sorted(at))
            for b in sorted(at)[index + 1 :]
            if at[a] != at[b] and at[a] != b and at[b] != a
        ]
        if not pairs:
            return out, objs, "not_applicable"
        a, b = rng.choice(pairs)
        a_loc, b_loc = at[a], at[b]
        a_pushed, b_pushed = was_pushed(facts, a), was_pushed(facts, b)
        clear_object(out, a)
        clear_object(out, b)
        out.update(placed_facts(a, b_loc, pushed=a_pushed))
        out.update(placed_facts(b, a_loc, pushed=b_pushed))
        return out, objs, "applied"
    if corruption == "goal_fact_added":
        missing = sorted(
            fact
            for fact in goal_facts - facts
            if subject_of(fact) in objs
            and parse_fact(fact)[0] in {"on", "pushed_to", "pressed"}
        )
        if not missing:
            return out, objs, "not_applicable"
        victim = rng.choice(missing)
        clear_object(out, str(subject_of(victim)))
        out.add(victim)
        return out, objs, "applied"
    if corruption == "phantom_object":
        objs.add(PHANTOM)
        out.add(f"on({PHANTOM}, {TABLE})")
        return out, objs, "applied"
    raise ValueError(f"Unknown corruption: {corruption}")


def physical(
    *,
    actions: list[ModelPlanAction],
    state: WorldState,
    goal: GoalSpec,
    valid_targets: set[str],
) -> dict[str, Any]:
    """Where the plan actually leaves the *true* world, ignoring the completeness rule.

    ``Validator`` rejects any plan that leaves an arm holding, including a hold that
    was already there before the suffix, so the frozen validity flag cannot tell
    "the world disagrees" apart from "our own convention refuses".  The state
    simulator can: it advances until the first genuine state-layer failure and the
    goal is then read off whatever prefix did run.
    """
    if not actions:
        return {
            "simulated_all_ok": True,
            "first_state_error": None,
            "goal_satisfied": bool(goal_satisfied(state, goal, set(ARMS))),
        }
    ok, _step, code, _msg, final_state = simulate_plan(
        ModelPlan(actions=actions), state, valid_targets=valid_targets
    )
    return {
        "simulated_all_ok": bool(ok),
        "first_state_error": code.value if code else None,
        "goal_satisfied": bool(goal_satisfied(final_state, goal, set(ARMS))),
    }


def score(
    *,
    actions: list[ModelPlanAction],
    validator: Validator,
    state: WorldState,
    goal: GoalSpec,
) -> dict[str, Any]:
    """Frozen scoring, plus the first error code when the plan is rejected."""
    valid, goal_ok, holds_before, _facts = evaluate_suffix(
        suffix=[action.model_dump() for action in actions],
        validator=validator,
        state=state,
        goal=goal,
    )
    error_code = layer = None
    if actions and not valid:
        result = validator.validate(ModelPlan(actions=actions), state)
        error_code = result.error_code.value if result.error_code else None
        layer = result.layer
    return {
        "valid": bool(valid),
        "goal_satisfied": bool(goal_ok),
        "goal_held_before": bool(holds_before),
        "error_code": error_code,
        "layer": layer,
    }


def _rate(flag: str, subset: list[dict[str, Any]]) -> Optional[float]:
    return sum(bool(row[flag]) for row in subset) / len(subset) if subset else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    control = {row["point_key"]: row for row in rows if row["corruption"] == "none"}
    out: dict[str, Any] = {}
    for corruption in dict.fromkeys(row["corruption"] for row in rows):
        sel = [row for row in rows if row["corruption"] == corruption]
        pool = [row for row in sel if row["corruption_status"] == "applied"] or sel
        paired = None
        if corruption != "none":
            only_control = sum(
                1
                for row in pool
                if row["truth_success"] is False
                and control.get(row["point_key"], {}).get("truth_success")
            )
            only_corrupted = sum(
                1
                for row in pool
                if row["truth_success"]
                and control.get(row["point_key"], {}).get("truth_success") is False
            )
            paired = {
                "only_control_success": only_control,
                "only_corrupted_success": only_corrupted,
                "mcnemar_exact_p_value": mcnemar_exact_p(only_control, only_corrupted),
            }
        out[corruption] = {
            "points": len(sel),
            "corruption_applied": sum(
                1 for row in sel if row["corruption_status"] == "applied"
            ),
            "search_success_rate": _rate("search_success", pool),
            "mean_plan_actions": (
                statistics.mean(row["plan_actions"] for row in pool) if pool else None
            ),
            "empty_plan_rate": (
                sum(1 for row in pool if row["plan_actions"] == 0) / len(pool)
                if pool
                else None
            ),
            "belief_success_rate": _rate("belief_success", pool),
            "truth_valid_rate": _rate("truth_valid", pool),
            "truth_success_rate": _rate("truth_success", pool),
            "truth_goal_physical_rate": _rate("truth_goal_physical", pool),
            "confidently_wrong_rate": _rate("confidently_wrong", pool),
            "false_alarm_rate": _rate("false_alarm", pool),
            "validator_refused_rate": _rate("validator_refused", pool),
            "confidently_wrong_points": sorted(
                row["point_key"] for row in pool if row["confidently_wrong"]
            )[:20],
            "mean_search_ms": (
                statistics.mean(row["search_ms"] for row in pool) if pool else None
            ),
            "truth_error_codes": dict(
                Counter(row["truth_error_code"] for row in pool if row["truth_error_code"]).most_common()
            ),
            "truth_state_errors": dict(
                Counter(
                    row["truth_state_error"] for row in pool if row["truth_state_error"]
                ).most_common()
            ),
            "paired_vs_none": paired,
            "by_perturbation": {
                perturbation: {
                    "points": len(sub),
                    "truth_success_rate": _rate("truth_success", sub),
                    "truth_goal_physical_rate": _rate("truth_goal_physical", sub),
                    "confidently_wrong_rate": _rate("confidently_wrong", sub),
                    "false_alarm_rate": _rate("false_alarm", sub),
                }
                for perturbation, sub in sorted(
                    (
                        (
                            name,
                            [row for row in pool if row["perturbation_type"] == name],
                        )
                        for name in sorted(
                            {row["perturbation_type"] for row in pool}
                        )
                    )
                )
            },
        }
    return out


def render(summary: dict[str, Any]) -> str:
    def fmt(value: Optional[float]) -> str:
        return "-" if value is None else f"{value:.4f}"

    lines = [
        "# 状态腐蚀扫描（审稿条目 C2 / P3）",
        "",
        f"- 点数 {summary['points']}，规划器 `{summary['planner_mode']}`，动作上限 {summary['max_actions']}",
        "- belief：验证器与目标都按**被相信的状态**判（系统自报口径）",
        "- truth success (frozen)：冻结口径，真实状态上 valid ∧ goal",
        "- truth goal (phys)：真实状态上用状态模拟器推进到首个 state 层错误后，目标事实是否成立",
        "  （冻结口径的 valid 还含『不得以持物结尾』的完整性规则，它会把『世界其实达成了目标』",
        "  与『世界不同意』混在一起，所以两列并报）",
        "- Conf. wrong：belief 判成功而真实世界目标未达成（最危险的一格）",
        "- False alarm：belief 判失败而真实世界已达标（过度保守，白修一次）",
        "- `none` 是控制条件，用来自检本脚本与 P1 的 `BFS_VALID` 是否一致",
        "",
        "## 腐蚀算子",
        "",
        "| Corruption | 语义 |",
        "|---|---|",
    ]
    for corruption in summary["by_corruption"]:
        lines.append(f"| {corruption} | {OPERATOR_NOTES[corruption]} |")
    lines += [
        "",
        "| Corruption | Applied | Search ok | Mean actions | Empty plan | belief success | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm | p vs none |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for corruption, cell in summary["by_corruption"].items():
        paired = cell.get("paired_vs_none")
        lines.append(
            f"| {corruption} | {cell['corruption_applied']}/{cell['points']} | "
            f"{fmt(cell['search_success_rate'])} | {fmt(cell['mean_plan_actions'])} | "
            f"{fmt(cell['empty_plan_rate'])} | {fmt(cell['belief_success_rate'])} | "
            f"{fmt(cell['truth_success_rate'])} | {fmt(cell['truth_goal_physical_rate'])} | "
            f"{fmt(cell['confidently_wrong_rate'])} | {fmt(cell['false_alarm_rate'])} | "
            f"{fmt(paired['mcnemar_exact_p_value']) if paired else '-'} |"
        )
    for corruption, cell in summary["by_corruption"].items():
        if corruption == "none" or not cell["by_perturbation"]:
            continue
        lines += [
            "",
            f"## {corruption}：按扰动类型分解",
            "",
            "| Perturbation | Points | truth success (frozen) | truth goal (phys) | Conf. wrong | False alarm |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for perturbation, sub in cell["by_perturbation"].items():
            lines.append(
                f"| {perturbation} | {sub['points']} | {fmt(sub['truth_success_rate'])} | "
                f"{fmt(sub['truth_goal_physical_rate'])} | "
                f"{fmt(sub['confidently_wrong_rate'])} | {fmt(sub['false_alarm_rate'])} |"
            )
        notes = []
        if cell["truth_error_codes"]:
            notes.append(
                f"冻结口径首错码：`{json.dumps(cell['truth_error_codes'], ensure_ascii=False)}`"
            )
        if cell["truth_state_errors"]:
            notes.append(
                f"真实 state 层首错码：`{json.dumps(cell['truth_state_errors'], ensure_ascii=False)}`"
            )
        notes.append(
            f"配对（冻结口径 success）：b={cell['paired_vs_none']['only_control_success']}, "
            f"c={cell['paired_vs_none']['only_corrupted_success']}"
        )
        lines += ["", *notes]
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
    parser.add_argument("--corruptions", nargs="+", default=list(CORRUPTIONS))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    for corruption in args.corruptions:
        if corruption not in CORRUPTIONS:
            raise ValueError(f"Unknown corruption: {corruption}")
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_dir}.")
    output_dir.mkdir(parents=True, exist_ok=False)

    formal_rows = read_jsonl(Path(args.formal))
    scenarios = {row["task_id"]: row for row in read_jsonl(Path(args.scenarios))}
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)

    points: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in formal_rows:
        if row.get("record_type") != "state_recovery":
            continue
        points.setdefault(
            (
                str(row["source_task_id"]),
                int(row["seed"]),
                str(row["perturbation_type"]),
            ),
            row,
        )
    selected = sorted(points.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))
    if args.limit:
        selected = selected[: args.limit]

    rows: list[dict[str, Any]] = []
    started = time.time()
    for (source_task_id, seed, perturbation), row in selected:
        scenario = scenarios.get(f"{source_task_id}__{perturbation}")
        if scenario is None:
            raise RuntimeError(f"missing scenario for {(source_task_id, seed, perturbation)}")
        goal = GoalSpec.model_validate(scenario["goal"])
        skills = allowed_skills_for(scenario)
        true_facts = set(row["observed_state_facts"])
        true_objects = set(scenario["objects"])
        true_state = state_from_facts(facts=true_facts, objects=true_objects)
        assert_consistent(true_facts, true_objects)
        point_key = f"{source_task_id}|{seed}|{perturbation}"

        for corruption in args.corruptions:
            believed_facts, believed_objects, status = apply_corruption(
                corruption,
                facts=true_facts,
                objects=true_objects,
                goal_facts=set(goal.facts),
                rng=random.Random(f"{point_key}|{corruption}"),
            )
            assert_consistent(believed_facts, believed_objects)
            believed_state = state_from_facts(
                facts=believed_facts, objects=believed_objects
            )
            validator.scene_objects = set(believed_objects)
            t0 = time.time()
            found, suffix, reason = search_suffix(
                state=believed_state,
                goal=goal,
                valid_targets=validator.valid_targets,
                allowed_skills=skills,
                mode=BFS_VALID,
                max_actions=args.max_actions,
            )
            search_ms = (time.time() - t0) * 1000.0
            actions = [ModelPlanAction.model_validate(item) for item in suffix]
            belief = score(
                actions=actions,
                validator=validator,
                state=believed_state,
                goal=goal,
            )
            validator.scene_objects = set(true_objects)
            truth = score(
                actions=actions, validator=validator, state=true_state, goal=goal
            )
            phys = physical(
                actions=actions,
                state=true_state,
                goal=goal,
                valid_targets=validator.valid_targets,
            )
            belief_success = bool(belief["valid"] and belief["goal_satisfied"])
            rows.append(
                {
                    "record_type": RECORD,
                    "schema_version": SCHEMA,
                    "point_key": point_key,
                    "source_task_id": source_task_id,
                    "seed": seed,
                    "perturbation_type": perturbation,
                    "task_family": scenario["task_family"],
                    "corruption": corruption,
                    "corruption_status": status,
                    "search_success": bool(found),
                    "search_reason": reason,
                    "search_ms": search_ms,
                    "plan_actions": len(actions),
                    "plan": [action.model_dump() for action in actions],
                    "true_facts": sorted(true_facts | true_state.empty_hand_facts(ARMS)),
                    "believed_facts": sorted(
                        believed_facts | believed_state.empty_hand_facts(ARMS)
                    ),
                    "belief_valid": belief["valid"],
                    "belief_goal": belief["goal_satisfied"],
                    "belief_success": belief_success,
                    "truth_valid": truth["valid"],
                    "truth_goal": truth["goal_satisfied"],
                    "truth_success": bool(truth["valid"] and truth["goal_satisfied"]),
                    "truth_error_code": truth["error_code"],
                    "truth_error_layer": truth["layer"],
                    "truth_sim_ok": phys["simulated_all_ok"],
                    "truth_state_error": phys["first_state_error"],
                    "truth_goal_physical": phys["goal_satisfied"],
                    "validator_refused": bool(belief_success and not truth["valid"]),
                    "confidently_wrong": bool(belief_success and not phys["goal_satisfied"]),
                    "false_alarm": bool(not belief_success and phys["goal_satisfied"]),
                }
            )

    summary = {
        "record_type": "state_corruption_summary",
        "schema_version": SCHEMA,
        "protocol": "state_recovery_428",
        "planner_mode": BFS_VALID,
        "points": len(selected),
        "rows": len(rows),
        "max_actions": args.max_actions,
        "corruptions": list(args.corruptions),
        "by_corruption": summarize(rows),
        "elapsed_s": time.time() - started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal": args.formal,
        "scenarios": args.scenarios,
    }

    write_jsonl(output_dir / f"{RECORD}s.jsonl", rows)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "analysis.md").write_text(render(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "points": summary["points"],
                "rows": summary["rows"],
                "elapsed_s": round(summary["elapsed_s"], 2),
                "truth_success": {
                    key: value["truth_success_rate"]
                    for key, value in summary["by_corruption"].items()
                },
                "confidently_wrong": {
                    key: value["confidently_wrong_rate"]
                    for key, value in summary["by_corruption"].items()
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
