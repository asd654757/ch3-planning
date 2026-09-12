#!/usr/bin/env python3
"""Paired interface-level MetaWorld comparison using frozen repair results.

No VLM is called.  Each repaired/stress plan is first revalidated symbolically;
only plans that are both valid and goal-satisfied are sent to MetaWorld.  Since
the current smoke simulator contains one movable object and one goal, each
symbolic pick/place pair is executed in a fresh episode.  A plan counts as
simulation-successful only when all of its pick/place pairs succeed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.capability.registry import load_registry
from ch3.compiler.executable_plan import compile_plan
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.schema.model_plan import ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator

PRESSURES = [
    "duplicate_pick_after_prefix",
    "unknown_object_after_prefix",
    "invalid_target_after_prefix",
    "place_before_pick",
    "repeat_pick_after_valid_plan",
]


def key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (str(row["task_id"]), int(row["seed"]), str(row["pressure_type"]))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def plan_hash(plan: ModelPlan) -> str:
    payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_seed(key: tuple[str, int, str], arm: str, pair_index: int) -> int:
    raw = "|".join(map(str, (*key, arm, pair_index))).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:4], "big") % 100000


def pairwise(actions: list[ModelPlanAction]) -> bool:
    if not actions or len(actions) % 2:
        return False
    for i in range(0, len(actions), 2):
        if actions[i].skill != Skill.PICK or actions[i + 1].skill != Skill.PLACE:
            return False
        if actions[i].object_id != actions[i + 1].object_id:
            return False
    return True


def scene_from_plan(plan: ModelPlan) -> set[str]:
    objects: set[str] = set()
    for action in plan.actions:
        objects.add(action.object_id)
        if action.skill == Skill.PLACE and action.target_id and action.target_id != "table":
            objects.add(action.target_id)
    return objects


def validate_plan(plan: ModelPlan, registry) -> tuple[bool, str | None, str | None]:
    scene = scene_from_plan(plan)
    validator = Validator(scene_objects=scene, registry=registry)
    result = validator.validate(plan, WorldState.table_scene(scene))
    return result.valid, result.error_code, result.layer


def select_points(
    rows: list[dict[str, Any]],
    per_pressure: int,
    *,
    all_slices: bool = False,
) -> list[tuple[str, int, str]]:
    by_pressure: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for row in rows:
        by_pressure[row["pressure_type"]].append(key(row))
    selected: list[tuple[str, int, str]] = []
    for pressure in PRESSURES:
        if all_slices:
            # Sim-IF-240 uses every frozen task x seed slice for each pressure.
            if len(by_pressure[pressure]) < per_pressure:
                raise RuntimeError(
                    f"only {len(by_pressure[pressure])} paired slices for {pressure}"
                )
            selected.extend(sorted(by_pressure[pressure])[:per_pressure])
        else:
            tasks: dict[str, tuple[str, int, str]] = {}
            for k in sorted(by_pressure[pressure]):
                # Keep the lowest seed for each task, then take a deterministic
                # task-diverse subset rather than repeated samples of one task.
                tasks.setdefault(k[0], k)
            if len(tasks) < per_pressure:
                raise RuntimeError(f"only {len(tasks)} distinct tasks for {pressure}")
            selected.extend(sorted(tasks.values())[:per_pressure])
    return selected


def execute_symbolic_plan(
    plan: ModelPlan,
    registry,
    point_key: tuple[str, int, str],
    arm: str,
    *,
    max_steps: int,
    observation_size: int,
) -> tuple[bool, list[dict[str, Any]]]:
    pair_results: list[dict[str, Any]] = []
    for pair_index in range(0, len(plan.actions), 2):
        pair_plan = ModelPlan(actions=plan.actions[pair_index : pair_index + 2])
        executable_pair = compile_plan(pair_plan, registry)
        executor = MetaWorldPlanExecutor(
            observation_size=observation_size,
            seed=stable_seed(point_key, arm, pair_index // 2),
        )
        try:
            executor.reset()
            result = executor.execute_plan(executable_pair, max_steps_per_primitive=max_steps)
            pair_results.append(
                {
                    "pair_index": pair_index // 2,
                    "success": result.success,
                    "step_results": [step.success for step in result.steps],
                    "final_puck_target_distance": result.final_puck_target_distance,
                    "elapsed_s": result.elapsed_s,
                    "sim_result": executor.result_to_dict(result),
                }
            )
        finally:
            executor.close()
    return all(item["success"] for item in pair_results), pair_results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--routed-source",
        type=Path,
        default=Path("data/collections/repair_pressure_formal_v11_routed_20260912_200252.jsonl"),
    )
    parser.add_argument(
        "--r2-source",
        type=Path,
        default=Path("data/collections/repair_pressure_formal_v8_no_oracle_20260911_153655.jsonl"),
    )
    parser.add_argument(
        "--checker-source",
        type=Path,
        default=Path("data/collections/external_baseline_formal_20260911_173240.jsonl"),
    )
    parser.add_argument(
        "--self-refine-source",
        type=Path,
        default=Path("data/collections/external_baseline_formal_20260911_173240.jsonl"),
    )
    parser.add_argument("--points-per-pressure", type=int, default=4)
    parser.add_argument(
        "--selection-mode",
        choices=("task_diverse", "all_slices"),
        default="task_diverse",
        help=(
            "task_diverse keeps the lowest seed for selected tasks; "
            "all_slices uses every task x seed slice, required for Sim-IF-240"
        ),
    )
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--output-dir", type=Path, default=Path("data/collections"))
    args = parser.parse_args()

    routed_rows = [
        r
        for r in load_jsonl(args.routed_source)
        if r.get("record_type") == "repair_pressure"
    ]
    r2_rows = [
        r
        for r in load_jsonl(args.r2_source)
        if r.get("record_type") == "repair_pressure" and r.get("repair_mode") == "R2"
    ]
    checker_rows = [
        r
        for r in load_jsonl(args.checker_source)
        if r.get("repair_mode") == "checker_loop"
    ]
    self_refine_rows = [
        r
        for r in load_jsonl(args.self_refine_source)
        if r.get("repair_mode") == "self_refine"
    ]

    routed = {key(r): r for r in routed_rows}
    r2 = {key(r): r for r in r2_rows}
    checker = {key(r): r for r in checker_rows}
    self_refine = {key(r): r for r in self_refine_rows}
    common = set(routed) & set(r2) & set(checker) & set(self_refine)
    candidates = [r for r in routed_rows if key(r) in common]
    points = select_points(
        candidates,
        args.points_per_pressure,
        all_slices=args.selection_mode == "all_slices",
    )
    if len(points) != len(PRESSURES) * args.points_per_pressure:
        raise RuntimeError("failed to select the expected paired pressure points")

    registry = load_registry("config/capability_registry.yaml")
    arms = ["NO_REPAIR", "R2_ONLY", "SELF_REFINE", "CHECKER_LOOP", "ROUTED"]
    records: list[dict[str, Any]] = []

    for point_index, point_key in enumerate(points, 1):
        routed_row = routed[point_key]
        r2_row = r2[point_key]
        checker_row = checker[point_key]
        self_refine_row = self_refine[point_key]
        print(
            f"[sim-compare] point={point_index}/{len(points)} "
            f"pressure={point_key[2]} task={point_key[0]} seed={point_key[1]} start",
            flush=True,
        )

        point_record: dict[str, Any] = {
            "record_type": "sim_compare_baseline_point",
            "point_index": point_index,
            "task_id": point_key[0],
            "seed": point_key[1],
            "pressure_type": point_key[2],
            "arms": {},
        }

        for arm in arms:
            if arm == "NO_REPAIR":
                source_row = routed_row
                plan_data = source_row.get("stress_plan")
            elif arm == "R2_ONLY":
                source_row = r2_row
                plan_data = source_row.get("model_plan")
            elif arm == "SELF_REFINE":
                source_row = self_refine_row
                plan_data = source_row.get("model_plan")
            elif arm == "CHECKER_LOOP":
                source_row = checker_row
                plan_data = source_row.get("model_plan")
            else:
                source_row = routed_row
                plan_data = source_row.get("model_plan")

            arm_record: dict[str, Any] = {
                "arm": arm,
                "source": str(source_row.get("output") or ""),
                "source_record_type": source_row.get("record_type"),
                "repair_mode": source_row.get("repair_mode"),
                "symbolic_valid": False,
                "symbolic_goal_satisfied": False,
                "sim_attempted": False,
                "sim_success": None,
                "pair_results": [],
                "error_code": None,
                "error_layer": None,
            }

            try:
                plan = ModelPlan.model_validate(plan_data)
                symbolic_valid, error_code, error_layer = validate_plan(plan, registry)
                arm_record["symbolic_valid"] = symbolic_valid
                arm_record["error_code"] = error_code
                arm_record["error_layer"] = error_layer
                arm_record["model_plan_hash"] = plan_hash(plan)
                # For repaired arms, also require the frozen symbolic result.
                if arm != "NO_REPAIR":
                    arm_record["symbolic_goal_satisfied"] = bool(
                        source_row.get("valid") and source_row.get("goal_satisfied")
                    )
                if (
                    symbolic_valid
                    and arm_record["symbolic_goal_satisfied"]
                    and pairwise(plan.actions)
                ):
                    arm_record["sim_attempted"] = True
                    sim_success, pair_results = execute_symbolic_plan(
                        plan,
                        registry,
                        point_key,
                        arm,
                        max_steps=args.max_steps_per_primitive,
                        observation_size=args.observation_size,
                    )
                    arm_record["sim_success"] = sim_success
                    arm_record["pair_results"] = pair_results
            except Exception as exc:
                arm_record["exception"] = f"{type(exc).__name__}: {exc}"

            arm_record["final_success"] = bool(
                arm_record["symbolic_valid"]
                and arm_record["symbolic_goal_satisfied"]
                and arm_record["sim_success"] is True
            )
            point_record["arms"][arm] = arm_record
            print(
                f"[sim-compare] point={point_index}/{len(points)} arm={arm} "
                f"symbolic={arm_record['symbolic_valid']}/{arm_record['symbolic_goal_satisfied']} "
                f"sim={arm_record['sim_success']} final={arm_record['final_success']}",
                flush=True,
            )

        records.append(point_record)

    summary_by_arm: dict[str, dict[str, Any]] = {}
    for arm in arms:
        arm_records = [r["arms"][arm] for r in records]
        pair_total = sum(len(r["pair_results"]) for r in arm_records)
        pair_success = sum(
            item["success"] for r in arm_records for item in r["pair_results"]
        )
        summary_by_arm[arm] = {
            "points": len(arm_records),
            "symbolic_valid": sum(r["symbolic_valid"] for r in arm_records),
            "symbolic_goal_satisfied": sum(r["symbolic_goal_satisfied"] for r in arm_records),
            "sim_attempted": sum(r["sim_attempted"] for r in arm_records),
            "sim_success": sum(r["sim_success"] is True for r in arm_records),
            "final_success": sum(r["final_success"] for r in arm_records),
            "final_success_rate": sum(r["final_success"] for r in arm_records) / len(arm_records),
            "pair_total": pair_total,
            "pair_success": pair_success,
            "pair_success_rate": pair_success / pair_total if pair_total else None,
        }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"sim_compare_baselines_{stamp}.json"
    summary = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_type": "sim_compare_baselines_summary",
        "source_routed": str(args.routed_source),
        "source_r2_only": str(args.r2_source),
        "source_checker_loop": str(args.checker_source),
        "points_per_pressure": args.points_per_pressure,
        "points": len(points),
        "vlm_calls": 0,
        "execution_protocol": "one_pick_place_pair_per_fresh_meta_world_episode",
        "summary_by_arm": summary_by_arm,
        "output": str(output_path),
    }
    output_path.write_text(
        json.dumps({**summary, "point_detail": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
