#!/usr/bin/env python3
"""Small interface-level simulation pilot using repaired plans from formal v11.

The MetaWorld smoke task has one movable object and one goal.  Therefore each
symbolic pick/place pair in a repaired plan is executed in a fresh MetaWorld
episode.  This is an execution-interface pilot, not a claim that all symbolic
objects coexist in the simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.capability.registry import load_registry
from ch3.compiler.executable_plan import compile_plan
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.schema.model_plan import Arm, ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator

ROUTE_PRIORITY: dict[str, list[tuple[str, ...]]] = {
    "duplicate_pick_after_prefix": [("R2",)],
    "unknown_object_after_prefix": [
        ("R2", "R1_FROM_STATE"),
        ("R2",),
    ],
    "invalid_target_after_prefix": [
        ("R2",),
        ("R2", "R1_FROM_STATE"),
    ],
    "place_before_pick": [("R2", "R1")],
    "repeat_pick_after_valid_plan": [("DETERMINISTIC_TRUNCATION",)],
}


def plan_hash(plan: ModelPlan) -> str:
    payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_pairwise_pick_place(actions: list[ModelPlanAction]) -> bool:
    if not actions or len(actions) % 2 != 0:
        return False
    for i in range(0, len(actions), 2):
        pick, place = actions[i], actions[i + 1]
        if pick.skill != Skill.PICK or place.skill != Skill.PLACE:
            return False
        if pick.object_id != place.object_id:
            return False
    return True


def scene_from_plan(plan: ModelPlan) -> set[str]:
    objects: set[str] = set()
    for action in plan.actions:
        objects.add(action.object_id)
        if action.skill == Skill.PLACE and action.target_id and action.target_id != "table":
            objects.add(action.target_id)
    return objects


def load_candidates(path: Path) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            row = json.loads(line)
            if row.get("record_type") != "repair_pressure":
                continue
            pressure = row.get("pressure_type")
            route = tuple(row.get("route_taken") or [])
            if pressure not in ROUTE_PRIORITY or route not in ROUTE_PRIORITY[pressure]:
                continue
            if not (row.get("valid") and row.get("goal_satisfied")):
                continue
            try:
                plan = ModelPlan.model_validate(row["model_plan"])
            except Exception:
                continue
            if not is_pairwise_pick_place(plan.actions):
                continue
            key = (pressure, route)
            if key in seen:
                continue
            seen.add(key)
            selected.append(
                {
                    "line_number": line_number,
                    "pressure_type": pressure,
                    "route_taken": list(route),
                    "task_id": row.get("task_id"),
                    "seed": row.get("seed"),
                    "fallback_triggered": row.get("fallback_triggered"),
                    "model_plan": plan,
                }
            )
    # Keep formal-v11 pressure ordering.
    order = {p: i for i, p in enumerate(ROUTE_PRIORITY)}
    selected.sort(key=lambda x: order[x["pressure_type"]])
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/collections/repair_pressure_formal_v11_routed_20260912_200252.jsonl"),
    )
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--output-dir", type=Path, default=Path("data/collections"))
    args = parser.parse_args()

    registry = load_registry("config/capability_registry.yaml")
    candidates = load_candidates(args.source)
    if len(candidates) < len(ROUTE_PRIORITY):
        raise RuntimeError(
            f"only found {len(candidates)} eligible cases, expected {len(ROUTE_PRIORITY)}"
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"sim_pilot_from_formal_v11_{stamp}.json"
    case_summaries: list[dict[str, Any]] = []

    for case_index, candidate in enumerate(candidates, 1):
        plan: ModelPlan = candidate["model_plan"]
        scene_objects = scene_from_plan(plan)
        initial_state = WorldState.table_scene(scene_objects)
        validator = Validator(scene_objects=scene_objects, registry=registry)
        validation = validator.validate(plan, initial_state)
        if not validation.valid:
            raise RuntimeError(
                f"formal-v11 plan failed local revalidation at line "
                f"{candidate['line_number']}: {validation.error_code} {validation.message}"
            )

        case_record: dict[str, Any] = {
            "record_type": "sim_pilot_formal_v11_case",
            "case_index": case_index,
            "source_line": candidate["line_number"],
            "pressure_type": candidate["pressure_type"],
            "route_taken": candidate["route_taken"],
            "task_id": candidate["task_id"],
            "seed": candidate["seed"],
            "fallback_triggered": candidate["fallback_triggered"],
            "model_plan_hash": plan_hash(plan),
            "symbolic_action_count": len(plan.actions),
            "execution_protocol": "one_pick_place_pair_per_fresh_meta_world_episode",
            "validation": {
                "valid": validation.valid,
                "error_code": validation.error_code,
                "error_layer": validation.layer,
            },
            "pairs": [],
        }

        print(
            f"[sim-pilot] case={case_index}/{len(candidates)} "
            f"pressure={candidate['pressure_type']} route={'/'.join(candidate['route_taken'])} start",
            flush=True,
        )

        for pair_index in range(0, len(plan.actions), 2):
            pair_plan = ModelPlan(actions=plan.actions[pair_index : pair_index + 2])
            executable_pair = compile_plan(pair_plan, registry)
            executor = MetaWorldPlanExecutor(
                observation_size=args.observation_size,
                seed=(candidate["seed"] or 0) * 100 + case_index * 10 + pair_index // 2,
            )
            try:
                reset_info = executor.reset()
                result = executor.execute_plan(
                    executable_pair,
                    max_steps_per_primitive=args.max_steps_per_primitive,
                )
                pair_record = {
                    "pair_index": pair_index // 2,
                    "symbolic_actions": pair_plan.model_dump(mode="json"),
                    "executable_plan": executable_pair.to_list(),
                    "success": result.success,
                    "step_results": [r.success for r in result.steps],
                    "final_puck_target_distance": result.final_puck_target_distance,
                    "elapsed_s": result.elapsed_s,
                    "reset": reset_info,
                    "sim_result": executor.result_to_dict(result),
                }
                case_record["pairs"].append(pair_record)
                print(
                    f"[sim-pilot] case={case_index}/{len(candidates)} "
                    f"pair={pair_index // 2 + 1}/{len(plan.actions) // 2} "
                    f"success={result.success} "
                    f"steps={[r.success for r in result.steps]} "
                    f"distance={result.final_puck_target_distance:.4f}",
                    flush=True,
                )
            finally:
                executor.close()

        case_record["pair_success_count"] = sum(p["success"] for p in case_record["pairs"])
        case_record["pair_total"] = len(case_record["pairs"])
        case_record["all_pairs_interface_success"] = (
            case_record["pair_success_count"] == case_record["pair_total"]
        )
        case_summaries.append(case_record)

    successful_pairs = sum(c["pair_success_count"] for c in case_summaries)
    total_pairs = sum(c["pair_total"] for c in case_summaries)
    successful_cases = sum(c["all_pairs_interface_success"] for c in case_summaries)
    summary = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_type": "sim_pilot_from_formal_v11_summary",
        "source": str(args.source),
        "cases": len(case_summaries),
        "successful_cases": successful_cases,
        "pairs": total_pairs,
        "successful_pairs": successful_pairs,
        "case_success_rate": successful_cases / len(case_summaries),
        "pair_success_rate": successful_pairs / total_pairs,
        "vlm_calls": 0,
        "execution_protocol": "one_pick_place_pair_per_fresh_meta_world_episode",
        "output": str(output_path),
    }
    output_path.write_text(
        json.dumps({**summary, "case_detail": case_summaries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
