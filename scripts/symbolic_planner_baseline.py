#!/usr/bin/env python3
"""Deterministic symbolic planner as a fifth baseline (reviewer item C5).

Searches the frozen symbolic transition model for a repair suffix and scores the
resulting plan through exactly the same evaluator used by the LLM arms, on both
frozen protocols:

- ``formal428``: State Recovery-428, symbol-layer scoring only.
- ``fair100``:   Fair-100 perturbed closed loop, adds MetaWorld execution.

Three search modes are reported side by side so the baseline is not a strawman:

- ``BFS_SHORT``: the existing recovery-feasibility oracle.  Any registered
  action sequence, shortest first, accepted on the goal test alone, and an empty
  suffix is returned when the goal already holds in the observed state.
  Reproduces the frozen P0 certificate definition exactly.
- ``BFS_VALID``: same search space, but a non-empty suffix is accepted only if it
  would also pass this paper's own symbolic evaluator, i.e. it may not end with
  an object still held.  This is the strongest deterministic planner the frozen
  action model admits, and is the headline number for reviewer item C5.
- ``BFS_EXEC``: ``BFS_VALID`` further restricted to the suffix shapes the frozen
  execution protocol can actually run in one fresh episode (pick/place pairs, or
  one push, or one press) and never empty.

Zero VLM calls, zero tokens.  Nothing in data/collections is modified.

Reading note for the ``fair100`` numbers: the frozen executor derives
``stable_seed`` from ``(task_id, seed, arm, pair_index)``, so every arm is scored
in its own re-randomised initial scene and one episode per arm.  All arms here
therefore share the single scene salt ``SYMBOLIC_BFS`` -- but that still means
the reported single-draw success is not a paired comparison with the frozen LLM
arms, and a plan that passes can fail under another salt.  Use
``scripts/reseed_stability_study.py`` for the paired, scene-controlled version.
"""

from __future__ import annotations

import argparse
import heapq
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlanAction, Skill
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.result import ValidationResult
from scripts.state_recovery_offline_analysis import (
    mcnemar_exact_p,
    read_jsonl,
    state_from_facts,
    write_json,
    write_jsonl,
)


ARMS = ("left", "right")
BFS_SHORT = "BFS_SHORT"
BFS_VALID = "BFS_VALID"
BFS_EXEC = "BFS_EXEC"
MODES = (BFS_SHORT, BFS_VALID, BFS_EXEC)
SIM_ARM = "SYMBOLIC_BFS"
SIM_SCHEMA = "2026-09-26-symbolic-planner-case-v1"


def action_spec(
    skill: str,
    object_id: str,
    target_id: Optional[str],
    arm: str,
    step_id: int,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "skill": skill,
        "object_id": object_id,
        "target_id": target_id,
        "arm": arm,
    }


def renumber(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**item, "step_id": index + 1} for index, item in enumerate(actions)
    ]


def _apply(
    state: WorldState,
    action: dict[str, Any],
    valid_targets: set[str],
) -> tuple[WorldState, bool]:
    try:
        plan_action = ModelPlanAction.model_validate({**action, "step_id": 1})
    except Exception:
        return state.copy(), False
    next_state, ok, _code, _message = step(state, plan_action, valid_targets=valid_targets)
    return next_state, ok


def state_key(state: WorldState) -> tuple[Any, ...]:
    return (
        tuple(sorted(state.holding.items())),
        tuple(sorted(state.at.items())),
        tuple(sorted(state.pushed)),
        tuple(sorted(state.pressed)),
        state.table_id,
    )


def single_step_expansions(
    state: WorldState,
    *,
    valid_targets: set[str],
    arms: Iterable[str],
    allowed_skills: set[str],
) -> list[tuple[list[dict[str, Any]], WorldState]]:
    """All legal one-action transitions, in deterministic order.

    The frozen simulator leaves ``at[obj]`` stale while an object is held, so
    ``is_on_table`` alone would let a second arm pick, push or press an object
    that is already in a hand.  This planner is deliberately stricter than that
    evaluator: an object in a hand is not a table object.  It can only make the
    baseline weaker, never stronger.
    """
    out: list[tuple[list[dict[str, Any]], WorldState]] = []
    held = set(state.holding.values())
    objects = sorted(state.objects)
    targets = sorted(state.objects | valid_targets)
    arm_set = tuple(arms)

    if "place" in allowed_skills:
        for arm in arm_set:
            obj = state.holding.get(arm)
            if obj is None:
                continue
            for target in targets:
                if target in held:
                    continue
                spec = action_spec("place", obj, target, arm, 1)
                nxt, ok = _apply(state, spec, valid_targets)
                if ok:
                    out.append(([spec], nxt))
    if "pick" in allowed_skills:
        for arm in arm_set:
            if not state.arm_empty(arm):
                continue
            for obj in objects:
                if obj in held:
                    continue
                spec = action_spec("pick", obj, None, arm, 1)
                nxt, ok = _apply(state, spec, valid_targets)
                if ok:
                    out.append(([spec], nxt))
    if "push" in allowed_skills:
        for arm in arm_set:
            if not state.arm_empty(arm):
                continue
            for obj in objects:
                if obj in held:
                    continue
                for target in targets:
                    if target in held:
                        continue
                    spec = action_spec("push", obj, target, arm, 1)
                    nxt, ok = _apply(state, spec, valid_targets)
                    if ok:
                        out.append(([spec], nxt))
    if "press" in allowed_skills:
        for arm in arm_set:
            if not state.arm_empty(arm):
                continue
            for obj in objects:
                if obj in held:
                    continue
                spec = action_spec("press", obj, None, arm, 1)
                nxt, ok = _apply(state, spec, valid_targets)
                if ok:
                    out.append(([spec], nxt))
    return out


def pair_expansions(
    state: WorldState,
    *,
    valid_targets: set[str],
    arms: Iterable[str],
) -> list[tuple[list[dict[str, Any]], WorldState]]:
    """Legal pick+place pairs on one empty arm -- the executable transport."""
    out: list[tuple[list[dict[str, Any]], WorldState]] = []
    held = set(state.holding.values())
    for arm in tuple(arms):
        if not state.arm_empty(arm):
            continue
        for obj in sorted(state.objects):
            if obj in held:
                continue
            picked, ok = _apply(
                state, action_spec("pick", obj, None, arm, 1), valid_targets
            )
            if not ok:
                continue
            for target in sorted(state.objects | valid_targets):
                if target in set(picked.holding.values()):
                    continue
                placed, ok = _apply(
                    picked, action_spec("place", obj, target, arm, 2), valid_targets
                )
                if not ok:
                    continue
                out.append(
                    (
                        [
                            action_spec("pick", obj, None, arm, 1),
                            action_spec("place", obj, target, arm, 2),
                        ],
                        placed,
                    )
                )
    return out


def family_of(allowed_skills: set[str]) -> str:
    if "pick" in allowed_skills:
        return "pick_place"
    if "push" in allowed_skills:
        return "push"
    if "press" in allowed_skills:
        return "press"
    return "unknown"


def search_suffix(
    *,
    state: WorldState,
    goal: GoalSpec,
    valid_targets: set[str],
    allowed_skills: set[str],
    mode: str,
    max_actions: int,
    arms: Iterable[str] = ARMS,
) -> tuple[bool, list[dict[str, Any]], str]:
    """Uniform-cost search for a suffix whose post-state satisfies the goal.

    ``arms`` is put in a sorted tuple before expansion: equal-cost plans are
    tie-broken by expansion order, and ``set(str)`` iteration order varies across
    processes, which would otherwise pick the acting arm of an interchangeable
    left/right plan by hash seed.
    """
    arm_order: tuple[str, ...] = tuple(sorted(arms))
    arm_set = set(arm_order)
    # An empty suffix is scored as valid by the frozen evaluator regardless of a
    # dangling hold, so both symbol-layer modes may return it.
    if mode != BFS_EXEC and goal_satisfied(state, goal, arm_set):
        return True, [], "goal_already_satisfied"

    family = family_of(allowed_skills)
    # BFS_EXEC may only emit the suffix shapes the frozen execution protocol can
    # run in one fresh episode: pick/place pairs, or one push, or one press.
    # Releasing a held object with a bare ``place`` is not one of them.
    expansion_skills = allowed_skills
    if mode == BFS_EXEC:
        expansion_skills = {
            "pick_place": {"pick", "place"},
            "push": {"push"},
            "press": {"press"},
        }.get(family, allowed_skills)
    single_only = mode == BFS_EXEC and family in {"push", "press"}
    dangling_hold_ok = mode == BFS_SHORT

    frontier: list[tuple[int, int, WorldState, list[dict[str, Any]]]] = []
    tie = 0
    frontier.append((0, tie, state.copy(), []))
    best: dict[tuple[Any, ...], int] = {state_key(state): 0}

    while frontier:
        cost, _t, current, plan = heapq.heappop(frontier)
        if cost >= max_actions:
            continue
        if mode == BFS_EXEC and family == "pick_place":
            expansions = [
                (renumber([*plan, *actions]), nxt)
                for actions, nxt in pair_expansions(
                    current, valid_targets=valid_targets, arms=arm_order
                )
            ]
        else:
            expansions = [
                (renumber([*plan, *actions]), nxt)
                for actions, nxt in single_step_expansions(
                    current,
                    valid_targets=valid_targets,
                    arms=arm_order,
                    allowed_skills=expansion_skills,
                )
            ]
        for candidate, nxt in expansions:
            if single_only and len(candidate) > 1:
                continue
            goal_ok = goal_satisfied(nxt, goal, arm_set)
            if goal_ok and (dangling_hold_ok or not nxt.holding):
                return True, candidate, "search_success"
            if len(candidate) < max_actions:
                key = state_key(nxt)
                if key not in best or best[key] > len(candidate):
                    best[key] = len(candidate)
                    tie += 1
                    heapq.heappush(frontier, (len(candidate), tie, nxt, candidate))
    return False, [], "no_plan_within_cap"


def evaluate_suffix(
    *,
    suffix: list[dict[str, Any]],
    validator: Validator,
    state: WorldState,
    goal: GoalSpec,
) -> tuple[bool, bool, bool, list[str]]:
    """Frozen State-Recovery scoring: validator pass + goal on post-state.

    An empty suffix is valid and is scored on the observed state, matching
    ``repair_pressure_state_recovery._evaluate_suffix`` with an empty suffix.
    ``holds_in_observed_state`` records whether the goal was already true
    before any repair action, which is what makes an empty suffix possible.
    """
    holds_before = bool(goal_satisfied(state, goal, validator.registry.arms))
    if not suffix:
        result = ValidationResult(valid=True, final_state=state.copy())
    else:
        from ch3.schema.model_plan import ModelPlan

        result = validator.validate(ModelPlan(actions=suffix), state)
    final_state = result.final_state if result.valid else None
    goal_ok = bool(
        result.valid
        and goal_satisfied(final_state or state, goal, validator.registry.arms)
    )
    final_facts = (
        sorted(final_state.facts() | final_state.empty_hand_facts(ARMS))
        if final_state is not None
        else []
    )
    return bool(result.valid), goal_ok, holds_before, final_facts


def is_sim_executable(actions: list[dict[str, Any]]) -> bool:
    """Mirror of ``sim_compare_baselines.is_executable_multiskill`` without MuJoCo."""
    skills = [item["skill"] for item in actions]
    if not skills:
        return False
    if all(skill in {"pick", "place"} for skill in skills):
        if len(skills) % 2:
            return False
        for index in range(0, len(skills), 2):
            if skills[index] != "pick" or skills[index + 1] != "place":
                return False
            if actions[index]["object_id"] != actions[index + 1]["object_id"]:
                return False
        return True
    if len(skills) == 1 and skills[0] in {"push", "press"}:
        return True
    return False


def allowed_skills_for(scenario: dict[str, Any]) -> set[str]:
    return set(scenario.get("hard_factors", {}).get("required_skills", [])) | {"place"}


def summarize_mode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = len(rows)
    by_family: dict[str, dict[str, Any]] = {}
    for family in sorted({str(row["task_family"]) for row in rows}):
        subset = [row for row in rows if str(row["task_family"]) == family]
        by_family[family] = _mode_counts(subset)
    by_perturbation: dict[str, dict[str, Any]] = {}
    for perturbation in sorted({str(row.get("perturbation_type")) for row in rows}):
        if perturbation == "None":
            continue
        subset = [
            row for row in rows if str(row.get("perturbation_type")) == perturbation
        ]
        by_perturbation[perturbation] = _mode_counts(subset)
    return {
        "mode": rows[0]["mode"] if rows else None,
        **_mode_counts(rows),
        "by_task_family": by_family,
        "by_perturbation": by_perturbation,
    }


def _mode_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points = len(rows)
    lengths = [float(row["suffix_action_count"]) for row in rows]
    return {
        "points": points,
        "search_success": sum(row["search_success"] for row in rows),
        "no_plan_within_cap": sum(
            row["search_reason"] == "no_plan_within_cap" for row in rows
        ),
        "empty_suffix_goal_holds": sum(
            row["search_reason"] == "goal_already_satisfied" for row in rows
        ),
        "symbolic_valid": sum(row["symbolic_valid"] for row in rows),
        "goal_satisfied": sum(row["goal_satisfied"] for row in rows),
        "final_success": sum(row["final_success"] for row in rows),
        "final_success_rate": (
            sum(row["final_success"] for row in rows) / points if points else None
        ),
        "sim_executable": sum(row["sim_executable_shape"] for row in rows),
        "sim_executable_rate": (
            sum(row["sim_executable_shape"] for row in rows) / points if points else None
        ),
        "sim_attempted": sum(bool(row.get("sim_attempted")) for row in rows),
        "sim_success": sum(bool(row.get("sim_success")) for row in rows),
        "mean_suffix_actions": statistics.fmean(lengths) if lengths else None,
        "median_suffix_actions": statistics.median(lengths) if lengths else None,
        "max_suffix_actions": max(lengths) if lengths else None,
        "mean_search_ms": (
            statistics.fmean([row["search_ms"] for row in rows]) if points else None
        ),
        "vlm_calls": 0,
        "tokens": 0,
    }


def paired_comparisons(
    *,
    cases: list[dict[str, Any]],
    references: dict[str, dict[tuple[str, ...], bool]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mode in MODES:
        mine = {
            tuple(row["point_key"]): bool(row["final_success"])
            for row in cases
            if row["mode"] == mode
        }
        per_reference: dict[str, Any] = {}
        for name, table in sorted(references.items()):
            common = sorted(set(mine) & set(table), key=str)
            counts = Counter((mine[key], table[key]) for key in common)
            only_mine = counts[(True, False)]
            only_other = counts[(False, True)]
            per_reference[name] = {
                "points": len(common),
                "SYMBOLIC_success": sum(mine[key] for key in common),
                "REFERENCE_success": sum(table[key] for key in common),
                "paired_counts": {
                    "both_success": counts[(True, True)],
                    "only_SYMBOLIC_success": only_mine,
                    f"only_{name}_success": only_other,
                    "both_fail": counts[(False, False)],
                },
                "discordant": only_mine + only_other,
                "mcnemar_exact_p_value": mcnemar_exact_p(only_mine, only_other),
            }
        out[mode] = per_reference
    return out


def load_formal_references(rows: list[dict[str, Any]]) -> dict[str, dict[tuple[str, ...], bool]]:
    references: dict[str, dict[tuple[str, ...], bool]] = defaultdict(dict)
    for row in rows:
        if row["baseline"] not in {"R2_STATE", "R1_FROM_STATE", "ROUTED"}:
            continue
        key = (str(row["source_task_id"]), int(row["seed"]), str(row["perturbation_type"]))
        references[str(row["baseline"])][key] = bool(row["recovery_success"])
    return dict(references)


def run_formal428(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    formal_rows = read_jsonl(Path(args.formal))
    scenarios = {row["task_id"]: row for row in read_jsonl(Path(args.scenarios))}
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)

    points: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in formal_rows:
        key = (str(row["source_task_id"]), int(row["seed"]), str(row["perturbation_type"]))
        points.setdefault(key, row)
    selected = sorted(points.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))
    if args.limit:
        selected = selected[: args.limit]

    certificates = {}
    if args.certificates and Path(args.certificates).exists():
        for row in read_jsonl(Path(args.certificates)):
            certificates[
                (
                    str(row["source_task_id"]),
                    int(row["seed"]),
                    str(row["perturbation_type"]),
                )
            ] = row

    cases: list[dict[str, Any]] = []
    for key, row in selected:
        source_task_id, seed, perturbation = key
        scenario = scenarios.get(f"{source_task_id}__{perturbation}")
        if scenario is None:
            raise RuntimeError(f"missing scenario for {key}")
        state = state_from_facts(
            facts=row["observed_state_facts"], objects=scenario["objects"]
        )
        goal = GoalSpec.model_validate(scenario["goal"])
        skills = allowed_skills_for(scenario)
        validator.scene_objects = set(scenario["objects"])
        valid_targets = validator.valid_targets
        for mode in MODES:
            t0 = _now()
            found, suffix, reason = search_suffix(
                state=state,
                goal=goal,
                valid_targets=valid_targets,
                allowed_skills=skills,
                mode=mode,
                max_actions=args.max_actions,
            )
            elapsed_ms = (_now() - t0) * 1000.0
            suffix = [ModelPlanAction.model_validate(item) for item in suffix]
            valid, goal_ok, holds_before, final_facts = evaluate_suffix(
                suffix=suffix, validator=validator, state=state, goal=goal
            )
            cert = certificates.get(key)
            cases.append(
                {
                    "record_type": "symbolic_planner_case",
                    "schema_version": SIM_SCHEMA,
                    "protocol": "formal428",
                    "mode": mode,
                    "point_key": [source_task_id, seed, perturbation],
                    "source_task_id": source_task_id,
                    "seed": seed,
                    "perturbation_type": perturbation,
                    "task_family": scenario["task_family"],
                    "goal_facts": scenario["goal"]["facts"],
                    "observed_state_facts": sorted(row["observed_state_facts"]),
                    "goal_holds_in_observed_state": holds_before,
                    "search_success": found,
                    "search_reason": reason,
                    "suffix_actions": [item.model_dump(mode="json") for item in suffix],
                    "suffix_action_count": len(suffix),
                    "symbolic_valid": valid,
                    "goal_satisfied": goal_ok,
                    "pass_but_wrong": bool(valid and not goal_ok),
                    "final_success": bool(valid and goal_ok),
                    "sim_executable_shape": is_sim_executable(
                        [item.model_dump(mode="json") for item in suffix]
                    ),
                    "final_state_facts": final_facts,
                    "oracle_length_from_certificate": (
                        cert["oracle_length"] if cert is not None else None
                    ),
                    "search_ms": elapsed_ms,
                }
            )

    references = load_formal_references(formal_rows)
    summary: dict[str, Any] = {
        "record_type": "symbolic_planner_summary",
        "protocol": "formal428",
        "points": len(selected),
        "completed_points": len({tuple(row["point_key"]) for row in cases}),
        "max_actions": args.max_actions,
        "by_mode": {
            mode: summarize_mode([row for row in cases if row["mode"] == mode])
            for mode in MODES
        },
        "paired_comparisons": paired_comparisons(cases=cases, references=references),
    }
    return cases, summary


def run_fair100(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from ch3.schema.model_plan import ModelPlan
    from scripts.fair_external_baselines_v2 import plan_evaluation, state_from_record

    source = json.load(Path(args.source).open(encoding="utf-8"))
    episodes = source["episode_detail"]
    if args.limit:
        episodes = episodes[: args.limit]
    tasks = {
        task["task_id"]: task
        for task in map(json.loads, Path(args.tasks).open(encoding="utf-8"))
    }
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)

    sim = None
    if not args.no_sim:
        from scripts.sim_compare_baselines import (
            execute_multiskill_plan,
            is_executable_multiskill,
        )

        sim = (execute_multiskill_plan, is_executable_multiskill)

    cases: list[dict[str, Any]] = []
    for episode in episodes:
        task = tasks[episode["task_id"]]
        previous = episode["attempts"][0]
        state = state_from_record(task, previous["symbolic_state"])
        goal = GoalSpec.model_validate(task["goal"])
        skills = allowed_skills_for(task)
        validator.scene_objects = set(task["objects"])
        for mode in MODES:
            t0 = _now()
            found, suffix, reason = search_suffix(
                state=state,
                goal=goal,
                valid_targets=validator.valid_targets,
                allowed_skills=skills,
                mode=mode,
                max_actions=args.max_actions,
            )
            elapsed_ms = (_now() - t0) * 1000.0
            actions = [ModelPlanAction.model_validate(item) for item in suffix]
            plan = ModelPlan(actions=actions) if actions else None
            validation, goal_ok = plan_evaluation(
                plan, task=task, validator=validator, state=state
            )
            symbolic_valid = bool(validation is not None and validation.valid)
            shape_ok = is_sim_executable(suffix)
            executable = sim is not None and bool(plan and symbolic_valid and goal_ok and shape_ok)
            sim_success = False
            if executable and plan is not None:
                # The frozen executor mixes the arm name into ``stable_seed``, so a
                # per-mode arm name would execute identical plans in different
                # initial scenes.  One shared salt keeps cross-mode differences
                # plan-driven; see data/reports/... for the re-seeded stability study.
                sim_success, _prims, _tasks = sim[0](
                    plan,
                    registry,
                    (str(episode["task_id"]), int(episode["seed"]), SIM_ARM),
                    SIM_ARM,
                    max_steps=args.max_steps_per_primitive,
                    observation_size=args.observation_size,
                )
            cases.append(
                {
                    "record_type": "symbolic_planner_case",
                    "schema_version": SIM_SCHEMA,
                    "protocol": "fair100",
                    "mode": mode,
                    "point_key": [str(episode["task_id"]), int(episode["seed"])],
                    "task_id": str(episode["task_id"]),
                    "seed": int(episode["seed"]),
                    "task_family": episode["task_family"],
                    "goal_facts": task["goal"]["facts"],
                    "observed_state_facts": sorted(
                        state.facts() | state.empty_hand_facts(ARMS)
                    ),
                    "source_final_success": bool(episode.get("final_success")),
                    "search_success": found,
                    "search_reason": reason,
                    "suffix_actions": [item.model_dump(mode="json") for item in actions],
                    "suffix_action_count": len(actions),
                    "symbolic_valid": symbolic_valid,
                    "goal_satisfied": bool(goal_ok),
                    "sim_executable_shape": shape_ok,
                    "sim_attempted": bool(executable),
                    "sim_success": bool(sim_success),
                    "final_success": bool(
                        symbolic_valid and goal_ok and shape_ok and sim_success
                    ),
                    "error_message": (
                        validation.message if validation is not None else "no_plan"
                    ),
                    "search_ms": elapsed_ms,
                }
            )
            print(
                json.dumps(
                    {
                        "mode": mode,
                        "task_id": episode["task_id"],
                        "family": episode["task_family"],
                        "n": len(actions),
                        "valid": symbolic_valid,
                        "goal": bool(goal_ok),
                        "shape": shape_ok,
                        "sim_attempted": bool(executable),
                        "sim_success": bool(sim_success),
                        "final": bool(
                            symbolic_valid and goal_ok and shape_ok and sim_success
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    references = _load_fair100_references(args, cases)
    summary: dict[str, Any] = {
        "record_type": "symbolic_planner_summary",
        "protocol": "fair100",
        "points": len(episodes),
        "sim_enabled": not args.no_sim,
        "max_actions": args.max_actions,
        "by_mode": {
            mode: summarize_mode([row for row in cases if row["mode"] == mode])
            for mode in MODES
        },
        "paired_comparisons": paired_comparisons(cases=cases, references=references),
    }
    return cases, summary


def _load_fair100_references(
    args: argparse.Namespace, cases: list[dict[str, Any]]
) -> dict[str, dict[tuple[str, ...], bool]]:
    from scripts.fair_routed_v2 import load_outcomes

    points = {(str(row["task_id"]), int(row["seed"])) for row in cases}
    outcomes = load_outcomes(
        fair_path=Path(args.fair_baselines) if args.fair_baselines else None,
        r1_path=Path(args.r1_reference) if args.r1_reference else None,
        fair_arms=args.fair_arms,
        points=points,
    )
    routed_path = Path(args.routed)
    if routed_path.exists():
        for line in routed_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("record_type") != "fair_routed_v2_case":
                continue
            key = (str(row["task_id"]), int(row["seed"]))
            if key in points:
                outcomes.setdefault("ROUTED", {})[key] = bool(row["final_success"])
    return {
        name: {
            (str(key[0]), int(key[1])): value
            for key, value in table.items()
            if (str(key[0]), int(key[1])) in points
        }
        for name, table in outcomes.items()
    }


def _now() -> float:
    return time.perf_counter()


def render_report(summary: dict[str, Any]) -> str:
    has_sim = bool(summary.get("sim_enabled", False))
    columns = [
        "Mode",
        "Points",
        "Search ok",
        "No plan",
        "Empty suffix (goal holds)",
        "Symbolic valid",
        "Goal ok",
        "Executable shape",
    ]
    if has_sim:
        columns += ["Sim attempted", "Sim success"]
    columns += ["Final success", "Rate", "Mean suffix actions", "Max suffix actions"]
    lines = [
        f"# Symbolic planner baseline - {summary['protocol']}",
        "",
    ]
    if not has_sim and summary["protocol"] == "fair100":
        lines += [
            "**SIMULATION DISABLED: ``final_success`` degenerates to 0 because the frozen",
            "Fair-100 protocol requires a MetaWorld success. Do not read the paired",
            "comparisons below as method results.**",
            "",
        ]
    lines += [
        f"- Points: **{summary['points']}**",
        f"- Search action cap: **{summary['max_actions']}**",
        "- VLM calls: **0**, tokens: **0**",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---:"] * len(columns)) + "|",
    ]
    for mode, row in summary["by_mode"].items():
        cells = [
            mode,
            row["points"],
            row["search_success"],
            row["no_plan_within_cap"],
            row["empty_suffix_goal_holds"],
            row["symbolic_valid"],
            row["goal_satisfied"],
            row["sim_executable"],
        ]
        if has_sim:
            cells += [row["sim_attempted"], row["sim_success"]]
        cells += [
            row["final_success"],
            f"{row['final_success_rate']:.4f}",
            f"{row['mean_suffix_actions']:.3f}",
            f"{row['max_suffix_actions']:.0f}",
        ]
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    lines.extend(["", "## Paired vs LLM arms", ""])
    for mode, per_reference in summary["paired_comparisons"].items():
        lines.append(f"### {mode}")
        lines.append("")
        lines.append("| Reference | Points | SYMBOLIC | REFERENCE | only SYMBOLIC | only REF | p (exact) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for name, row in per_reference.items():
            counts = row["paired_counts"]
            lines.append(
                f"| {name} | {row['points']} | {row['SYMBOLIC_success']} | "
                f"{row['REFERENCE_success']} | {counts['only_SYMBOLIC_success']} | "
                f"{counts[f'only_{name}_success']} | {row['mcnemar_exact_p_value']:.6g} |"
            )
        lines.append("")
    for bucket_key, title in (
        ("by_perturbation", "By perturbation"),
        ("by_task_family", "By task family"),
    ):
        first = next(iter(summary["by_mode"].values()))
        if not first.get(bucket_key):
            continue
        lines.extend(["", f"## {title}", ""])
        names = sorted(set().union(*[set(row[bucket_key]) for row in summary["by_mode"].values()]))
        header = f"| {title} | Mode | Points | Final success | Rate | Executable shape | Mean suffix actions |"
        lines.extend([header, "|---|---|---:|---:|---:|---:|---:|"])
        for name in names:
            for mode, row in summary["by_mode"].items():
                cell = row[bucket_key].get(name)
                if cell is None:
                    continue
                lines.append(
                    f"| {name} | {mode} | {cell['points']} | {cell['final_success']} | "
                    f"{cell['final_success_rate']:.4f} | {cell['sim_executable']} | "
                    f"{cell['mean_suffix_actions']:.3f} |"
                )
    lines.extend(
        [
            "",
            "## Reading notes",
            "",
            "- The planner is handed the frozen closed-world symbolic state and the goal facts, so "
            "no perception and no natural-language grounding is needed to instantiate the search. "
            "These are upper bounds for the deterministic route, not deployment numbers.",
            "- `BFS_SHORT` accepts on the goal test alone, mirroring the frozen feasibility "
            "certificate definition.",
            "- `BFS_VALID` additionally rejects a suffix that ends with an object still held, i.e. "
            "it searches for a suffix that passes this paper's own symbolic evaluator.",
            "- `BFS_EXEC` additionally requires a suffix shape the frozen execution protocol can run "
            "as one fresh episode (pick/place pairs, or a single push, or a single press).",
            "- A returned empty suffix is scored as valid with zero repair actions under the frozen "
            "evaluator, which bypasses the held-object completeness rule.",
            "- Search cost is reported in milliseconds; model calls and tokens are zero by "
            "construction.",
            "- Headline arm per protocol: `formal428` -> `BFS_VALID` (symbolic-layer scoring); "
            "`fair100` -> `BFS_EXEC` (end-to-end scoring with MetaWorld execution).",
            "",
        ]
    )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=["formal428", "fair100"], required=True)
    parser.add_argument(
        "--formal",
        default="data/collections/state_recovery_formal_v1_20260914_101157.jsonl",
    )
    parser.add_argument(
        "--scenarios", default="data/scenarios/state_recovery_tasks_v1.jsonl"
    )
    parser.add_argument(
        "--certificates",
        default="data/reports/state_recovery_offline_p0p1_20260916_101250/feasibility_certificates.jsonl",
    )
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
        "--routed", default="data/collections/fair_routed_v2_20260916_102135.jsonl"
    )
    parser.add_argument(
        "--fair-baselines",
        default="data/collections/fair_external_baselines_v2_20260915_025622.jsonl",
    )
    parser.add_argument(
        "--r1-reference",
        default="data/collections/visual_prompt_v2_repair_rerun_20260914_210737.jsonl",
    )
    parser.add_argument(
        "--fair-arms", nargs="+", default=["SELF_REFINE_STATE_V2", "CHECKER_LOOP_STATE_V2"]
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-actions", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-sim", action="store_true")
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Output directory is not empty: {output_dir}. Use a new timestamp."
        )
    output_dir.mkdir(parents=True, exist_ok=False)

    started = _now()
    if args.protocol == "formal428":
        cases, summary = run_formal428(args)
        references = load_formal_references(read_jsonl(Path(args.formal)))
    else:
        cases, summary = run_fair100(args)
        references = _load_fair100_references(args, cases)

    summary["elapsed_s"] = _now() - started
    summary["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["reference_points_by_arm"] = {
        name: len(table) for name, table in references.items()
    }

    write_jsonl(output_dir / "symbolic_planner_cases.jsonl", cases)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "analysis.md").write_text(render_report(summary), encoding="utf-8")
    print(json.dumps(summary["by_mode"], ensure_ascii=False, indent=2))
    print(json.dumps({"output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
