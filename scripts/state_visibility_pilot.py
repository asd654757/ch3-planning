#!/usr/bin/env python3
"""State visibility pilot on frozen perturbed closed-loop failures."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.collector import world_state_from_task
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.repair import PlanRepairer
from ch3.logger.episode_logger import EpisodeLogger
from scripts.sim_compare_baselines import execute_multiskill_plan, is_executable_multiskill


SPARSE_ARMS = ("SPARSE_STATE", "SPARSE_STATE_VISUAL")
ALL_ARMS = ("FULL_STATE",) + SPARSE_ARMS


def state_from_record(task: dict[str, Any], record: dict[str, Any]) -> WorldState:
    state = WorldState.table_scene(task["objects"])
    state.at.update(record.get("at", {}))
    state.holding.update(record.get("holding", {}))
    state.pushed = set(record.get("pushed", []))
    state.pressed = set(record.get("pressed", []))
    return state


def build_validation(source_task: dict[str, Any], episode: dict[str, Any]) -> ValidationResult:
    previous = episode["attempts"][0]
    current_state = state_from_record(source_task, previous["symbolic_state"])
    return ValidationResult(
        valid=False,
        first_invalid_step=1,
        error_code=None,
        message="metaworld_execution_failure",
        layer="execution",
        validated_prefix=[],
        final_state=current_state,
    )


def repair_point(
    *,
    repairer: PlanRepairer,
    validator: Validator,
    registry: CapabilityRegistry,
    source_task: dict[str, Any],
    episode: dict[str, Any],
    arm: str,
    temperature: float,
    max_steps: int,
    observation_size: int,
) -> dict[str, Any]:
    task_id = str(episode["task_id"])
    seed = int(episode["seed"])
    previous = episode["attempts"][0]
    failed_plan = ModelPlan.model_validate(previous["plan"])
    validation_input = build_validation(source_task, episode)
    generation = PlanGeneration(
        failed_plan,
        None,
        previous.get("generation", {}).get("parse_error"),
        "",
        "frozen_initial_generation",
        str(previous.get("generation", {}).get("prompt_hash", "")),
        previous.get("generation", {}).get("infeasible_reason"),
    )

    repair_task = dict(source_task)
    repair_task["state_visibility"] = "sparse"
    if arm == "SPARSE_STATE_VISUAL":
        failure_frame = previous.get("failure_frame")
        if not failure_frame or not Path(failure_frame).is_file():
            raise RuntimeError(f"missing failure frame for {task_id} seed={seed}")
        repair_task["image_path"] = failure_frame
        repair_task["visual_feedback"] = True
    elif arm != "SPARSE_STATE":
        raise ValueError(f"unsupported arm: {arm}")

    started = time.time()
    repair = repairer.repair(
        repair_mode="R1_FROM_STATE",
        task=repair_task,
        initial_generation=generation,
        validation=validation_input,
        initial_state=world_state_from_task(source_task),
        seed=seed,
        temperature=temperature,
    )
    repair_elapsed = time.time() - started

    validator.scene_objects = set(source_task["objects"])
    initial_state = world_state_from_task(source_task)
    validation_result = (
        validator.validate(repair.plan, initial_state)
        if repair.plan is not None
        else None
    )
    symbolic_valid = bool(validation_result.valid) if validation_result is not None else False
    goal_satisfied_result = (
        bool(
            goal_satisfied(
                validation_result.final_state,
                GoalSpec.model_validate(source_task["goal"]),
                {"left", "right"},
            )
        )
        if validation_result is not None and validation_result.valid
        else False
    )

    sim_attempted = False
    sim_success = False
    sim_tasks: list[str] = []
    primitive_results: list[dict[str, Any]] = []
    if repair.plan is not None and symbolic_valid and goal_satisfied_result and is_executable_multiskill(repair.plan.actions):
        sim_attempted = True
        sim_started = time.time()
        sim_success, primitive_results, sim_tasks = execute_multiskill_plan(
            repair.plan,
            registry,
            (task_id, seed, arm),
            arm,
            max_steps=max_steps,
            observation_size=observation_size,
        )
        sim_elapsed = time.time() - sim_started
    else:
        sim_elapsed = 0.0

    compact_primitives = [
        {
            "segment_index": item.get("segment_index"),
            "success": bool(item.get("success")),
            "step_results": item.get("step_results", []),
            "final_puck_target_distance": item.get("final_puck_target_distance"),
            "elapsed_s": item.get("elapsed_s"),
        }
        for item in primitive_results
    ]

    return {
        "record_type": "state_visibility_pilot_case",
        "task_id": task_id,
        "task_family": source_task.get("task_family"),
        "seed": seed,
        "arm": arm,
        "source_arm": "VISUAL_PROMPT_V1",
        "source_final_success": bool(episode.get("final_success")),
        "repair_mode": "R1_FROM_STATE",
        "state_visibility": "sparse",
        "visual_frame": arm == "SPARSE_STATE_VISUAL",
        "prompt_version": "state_visibility_pilot_sparse_v1",
        "accepted": repair.accepted,
        "reject_reason": repair.reject_reason,
        "parse_error": repair.parse_error,
        "symbolic_valid": symbolic_valid,
        "goal_satisfied": goal_satisfied_result,
        "error_code": (
            validation_result.error_code.name
            if validation_result is not None and validation_result.error_code is not None
            else None
        ),
        "error_layer": validation_result.layer if validation_result is not None else None,
        "error_message": validation_result.message if validation_result is not None else "parse_error",
        "plan": repair.plan.model_dump(mode="json") if repair.plan is not None else None,
        "sim_attempted": sim_attempted,
        "sim_success": bool(sim_success),
        "final_success": bool(symbolic_valid and goal_satisfied_result and sim_success),
        "sim_tasks": sim_tasks,
        "primitive_results": compact_primitives,
        "repair_elapsed_s": repair_elapsed,
        "sim_elapsed_s": sim_elapsed,
        "response": {
            "model": repair.response.model,
            "prompt_tokens": repair.response.prompt_tokens,
            "completion_tokens": repair.response.completion_tokens,
            "total_tokens": repair.response.total_tokens,
            "finish_reason": repair.response.finish_reason,
        },
    }


def summarize(
    sparse_records: list[dict[str, Any]],
    full_records: list[dict[str, Any]],
) -> dict[str, Any]:
    all_records = full_records + sparse_records
    by_arm: dict[str, dict[str, Any]] = {}
    by_arm_family: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    records_by_point = {
        (r["task_id"], r["seed"], r["arm"]): r for r in all_records
    }
    for arm in ALL_ARMS:
        rows = [r for r in all_records if r["arm"] == arm]
        stats: dict[str, Any] = {
            "points": len(rows),
            "symbolic_valid": sum(r["symbolic_valid"] for r in rows),
            "goal_satisfied": sum(r["goal_satisfied"] for r in rows),
            "sim_attempted": sum(r["sim_attempted"] for r in rows),
            "sim_success": sum(r["sim_success"] for r in rows),
            "final_success": sum(r["final_success"] for r in rows),
            "parse_error": sum(r["parse_error"] is not None for r in rows),
            "schema_error": sum((r.get("error_code") or "") == "SCHEMA_ERROR" for r in rows),
            "prompt_tokens": sum(r.get("response", {}).get("prompt_tokens", 0) for r in rows),
            "completion_tokens": sum(r.get("response", {}).get("completion_tokens", 0) for r in rows),
            "total_tokens": sum(r.get("response", {}).get("total_tokens", 0) for r in rows),
        }
        stats["final_success_rate"] = (
            stats["final_success"] / stats["points"] if stats["points"] else None
        )
        by_arm[arm] = stats
        for row in rows:
            by_arm_family[(arm, row["task_family"])]["points"] += 1
            by_arm_family[(arm, row["task_family"])]["final_success"] += int(row["final_success"])

    by_family: dict[str, dict[str, Any]] = {}
    for (arm, family), counts in sorted(by_arm_family.items()):
        by_family.setdefault(family, {})[arm] = {
            "points": counts["points"],
            "final_success": counts["final_success"],
            "final_success_rate": counts["final_success"] / counts["points"] if counts["points"] else None,
        }

    paired: Counter[str] = Counter()
    task_ids = sorted({r["task_id"] for r in all_records})
    seeds = sorted({r["seed"] for r in all_records})
    for task_id in task_ids:
        for seed in seeds:
            full_row = records_by_point.get((task_id, seed, "FULL_STATE"))
            sparse_row = records_by_point.get((task_id, seed, "SPARSE_STATE"))
            visual_row = records_by_point.get((task_id, seed, "SPARSE_STATE_VISUAL"))
            if full_row is None or sparse_row is None or visual_row is None:
                continue
            outcomes = {
                "FULL_STATE": full_row["final_success"],
                "SPARSE_STATE": sparse_row["final_success"],
                "SPARSE_STATE_VISUAL": visual_row["final_success"],
            }
            key = "_".join(
                arm if success else "not_" + arm
                for arm, success in outcomes.items()
            )
            paired[key] += 1
    return {"by_arm": by_arm, "by_family": by_family, "paired_final_success": dict(paired)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--env-file", default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env")
    parser.add_argument("--source", default="data/collections/sim_closed_loop_visualstate_perturbed100_20260914_115417.json")
    parser.add_argument("--full-state-results", default="data/collections/visual_prompt_v2_repair_rerun_20260914_210737.jsonl")
    parser.add_argument("--tasks", default="data/scenarios/multiskill_tasks_v1.jsonl")
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    args = parser.parse_args()

    source_tasks = {
        task["task_id"]: task
        for task in map(json.loads, Path(args.tasks).open(encoding="utf-8"))
    }
    source = json.load(Path(args.source).open(encoding="utf-8"))
    source_by_key = {
        (episode["task_id"], int(episode["seed"])): episode
        for episode in source["episode_detail"]
    }

    manifest = json.load(Path(args.manifest).open(encoding="utf-8"))
    selected_keys = [(row["task_id"], int(row["seed"])) for row in manifest["points"]]
    if len(selected_keys) != len(set(selected_keys)):
        raise RuntimeError("manifest contains duplicate task/seed points")
    missing = [key for key in selected_keys if key not in source_by_key]
    if missing:
        raise RuntimeError(f"manifest points missing from source: {missing[:3]}")
    episodes = [source_by_key[key] for key in selected_keys]
    if not episodes:
        raise RuntimeError("no source episodes selected")

    full_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for line in Path(args.full_state_results).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("record_type") != "visual_prompt_v2_repair_rerun_case":
            continue
        if record.get("arm") != "STATE_PROMPT_V2":
            continue
        full_by_key[(record["task_id"], int(record["seed"]))] = record
    missing_full = [key for key in selected_keys if key not in full_by_key]
    if missing_full:
        raise RuntimeError(f"missing FULL_STATE records: {missing_full[:3]}")

    for episode in episodes:
        frame = episode["attempts"][0].get("failure_frame")
        if not frame or not Path(frame).is_file():
            raise RuntimeError(f"missing visual failure frame for {episode['task_id']} seed={episode['seed']}")

    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    client = DashScopeVLMClient(model=args.model, env_path=args.env_file)
    repairer = PlanRepairer(client, max_tokens=args.max_tokens, json_mode=True)
    logger = EpisodeLogger(args.output)
    started = time.time()

    print(
        json.dumps(
            {
                "record_type": "state_visibility_pilot_start",
                "source": args.source,
                "manifest": args.manifest,
                "points": len(episodes),
                "sparse_arms": list(SPARSE_ARMS),
                "expected_vlm_calls": len(episodes) * len(SPARSE_ARMS),
                "full_state_source": args.full_state_results,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    for index, episode in enumerate(episodes, 1):
        source_task = source_tasks[episode["task_id"]]
        for arm in SPARSE_ARMS:
            print(
                f"[state-visibility] point={index}/{len(episodes)} arm={arm} "
                f"task={episode['task_id']} seed={episode['seed']} start",
                flush=True,
            )
            record = repair_point(
                repairer=repairer,
                validator=validator,
                registry=registry,
                source_task=source_task,
                episode=episode,
                arm=arm,
                temperature=args.temperature,
                max_steps=args.max_steps_per_primitive,
                observation_size=args.observation_size,
            )
            logger.append(record)
            print(
                json.dumps(
                    {
                        "task_id": record["task_id"],
                        "seed": record["seed"],
                        "arm": record["arm"],
                        "symbolic_valid": record["symbolic_valid"],
                        "goal_satisfied": record["goal_satisfied"],
                        "sim_success": record["sim_success"],
                        "final_success": record["final_success"],
                        "error_code": record["error_code"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    sparse_records = []
    for line in Path(args.output).read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("record_type") == "state_visibility_pilot_case":
            sparse_records.append(record)
    full_records = []
    for key in selected_keys:
        full_record = dict(full_by_key[key])
        full_record["record_type"] = "state_visibility_pilot_full_state_reference"
        full_record["arm"] = "FULL_STATE"
        full_record["state_visibility"] = "full"
        full_records.append(full_record)

    summary = {
        "record_type": "state_visibility_pilot_summary",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "manifest": args.manifest,
        "points": len(episodes),
        "arms": list(ALL_ARMS),
        "vlm_calls": len(sparse_records),
        "prompt_version": "state_visibility_pilot_sparse_v1",
        "repair_mode": "R1_FROM_STATE",
        "execution_protocol": "frozen_initial_failure_repair_then_metaworld_execution",
        "state_visibility_definition": {
            "FULL_STATE": "structured post-execution state plus derived transports",
            "SPARSE_STATE": "instruction, closed-world objects, goal facts; no post-execution state",
            "SPARSE_STATE_VISUAL": "sparse symbolic feedback plus failure frame as advisory observation",
        },
        "elapsed_s": time.time() - started,
        **summarize(sparse_records, full_records),
        "output": args.output,
    }
    logger.append(summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
