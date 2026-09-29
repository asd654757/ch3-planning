#!/usr/bin/env python3
"""Strict paired sparse-state versus bounded visual execution feedback pilot.

Both method variants receive the same frozen failed episode and failure frame.
The only intended input difference is whether the frame is attached to the
repair request.  This is execution-state feedback simulation, not vision-only
robot control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.validator.pipeline import Validator
from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.collector import world_state_from_task
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.repair import PlanRepairer
from ch3.vlm.client import VLMResponse
from ch3.validator.result import ValidationResult
from scripts.sim_compare_baselines import execute_multiskill_plan, is_executable_multiskill


ARMS = ("SPARSE_STATE", "SPARSE_STATE_VISUAL")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def case_key(task_id: str, seed: int) -> str:
    return f"{task_id}::seed={seed}"


def compact_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def repair_case(
    *, repairer: PlanRepairer, validator: Validator, registry: CapabilityRegistry,
    task: dict[str, Any], episode: dict[str, Any], arm: str, max_steps: int,
    observation_size: int, temperature: float,
) -> dict[str, Any]:
    task_id = str(episode["task_id"])
    seed = int(episode["seed"])
    previous = episode["attempts"][0]
    frame = previous.get("failure_frame")
    if not frame or not Path(frame).is_file():
        raise RuntimeError(f"missing shared failure frame: {task_id} seed={seed}: {frame}")
    failed_plan = ModelPlan.model_validate(previous["plan"])
    initial_state = world_state_from_task(task)
    validation = ValidationResult(
        valid=False, first_invalid_step=1, error_code=None,
        message="metaworld_execution_failure", layer="execution",
        validated_prefix=[], final_state=initial_state,
    )
    generation = PlanGeneration(
        failed_plan,
        VLMResponse(model="frozen_source", content="", finish_reason="stop", latency_ms=0),
        None, "", "frozen_initial_generation", "", None,
    )
    repair_task = dict(task)
    repair_task.update({"state_visibility": "sparse", "pair_id": case_key(task_id, seed)})
    if arm == "SPARSE_STATE_VISUAL":
        repair_task.update({
            "image_path": frame,
            "visual_feedback": True,
            "visual_feedback_protocol": "bounded_execution_observation_v1",
        })
    started = time.time()
    repair = repairer.repair(
        repair_mode="R1_FROM_STATE", task=repair_task,
        initial_generation=generation, validation=validation,
        initial_state=initial_state, seed=seed, temperature=temperature,
    )
    validator.scene_objects = set(task["objects"])
    checked = validator.validate(repair.plan, initial_state) if repair.plan is not None else None
    valid = bool(checked and checked.valid)
    goal_ok = bool(checked and valid and goal_satisfied(
        checked.final_state, GoalSpec.model_validate(task["goal"]), {"left", "right"}
    ))
    sim_attempted = bool(repair.plan and valid and goal_ok and is_executable_multiskill(repair.plan.actions))
    sim_success = False
    primitive_results: list[dict[str, Any]] = []
    sim_tasks: list[str] = []
    if sim_attempted:
        sim_success, primitive_results, sim_tasks = execute_multiskill_plan(
            repair.plan, registry, (task_id, seed, arm), arm,
            max_steps=max_steps, observation_size=observation_size,
        )
    return {
        "record_type": "visual_feedback_paired_case",
        "pair_id": case_key(task_id, seed), "task_id": task_id, "seed": seed,
        "task_family": task.get("task_family"), "arm": arm,
        "state_visibility": "sparse", "visual_frame_used": arm == "SPARSE_STATE_VISUAL",
        "visual_feedback_protocol": "bounded_execution_observation_v1" if arm == "SPARSE_STATE_VISUAL" else None,
        "shared_failure_frame": str(Path(frame).resolve()),
        "shared_failure_frame_hash": compact_hash(Path(frame).read_bytes().hex()),
        "source_plan_hash": compact_hash(previous["plan"]),
        "accepted": repair.accepted, "reject_reason": repair.reject_reason,
        "parse_error": repair.parse_error, "valid": valid, "goal_satisfied": goal_ok,
        "sim_attempted": sim_attempted, "sim_success": bool(sim_success),
        "final_success": bool(valid and goal_ok and sim_success),
        "plan_hash": compact_hash(repair.plan.model_dump(mode="json")) if repair.plan else None,
        "plan": repair.plan.model_dump(mode="json") if repair.plan else None,
        "primitive_results": [{"success": x.get("success"), "step_results": x.get("step_results", [])} for x in primitive_results],
        "sim_tasks": sim_tasks, "model_calls": 1,
        "response": {"model": repair.response.model, "prompt_tokens": repair.response.prompt_tokens,
                     "completion_tokens": repair.response.completion_tokens, "total_tokens": repair.response.total_tokens},
        "elapsed_s": time.time() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--env-file", default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env")
    parser.add_argument("--source", required=True)
    parser.add_argument("--tasks", default="data/scenarios/multiskill_tasks_v1.jsonl")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    args = parser.parse_args()
    tasks = {str(x["task_id"]): x for x in load_jsonl(Path(args.tasks))}
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    episodes = {(str(x["task_id"]), int(x["seed"])): x for x in source["episode_detail"]}
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    points = [(str(x["task_id"]), int(x["seed"])) for x in manifest["points"]]
    if len(points) != len(set(points)):
        raise RuntimeError("manifest contains duplicate paired points")
    selected = [(episodes[k], tasks[k[0]]) for k in points if k in episodes and k[0] in tasks]
    if len(selected) != len(points):
        raise RuntimeError("manifest point missing from source or task definitions")
    for episode, _task in selected:
        frame = episode["attempts"][0].get("failure_frame")
        if not frame or not Path(frame).is_file():
            raise RuntimeError(f"invalid failure frame for {episode['task_id']} seed={episode['seed']}")
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    repairer = PlanRepairer(DashScopeVLMClient(model=args.model, env_path=args.env_file), max_tokens=args.max_tokens, json_mode=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with output.open("w", encoding="utf-8") as handle:
        for index, (episode, task) in enumerate(selected, 1):
            for arm in ARMS:
                print(f"[visual-paired] point={index}/{len(selected)} arm={arm} task={episode['task_id']} seed={episode['seed']} start", flush=True)
                record = repair_case(repairer=repairer, validator=validator, registry=registry, task=task, episode=episode, arm=arm, max_steps=args.max_steps, observation_size=args.observation_size, temperature=args.temperature)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n"); handle.flush()
                print(json.dumps({k: record[k] for k in ("pair_id", "arm", "valid", "goal_satisfied", "sim_success", "final_success", "parse_error")}, ensure_ascii=False), flush=True)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        by_arm = {arm: {"points": sum(r["arm"] == arm for r in rows), "final_success": sum(r["arm"] == arm and r["final_success"] for r in rows), "parse_error": sum(r["arm"] == arm and r["parse_error"] is not None for r in rows)} for arm in ARMS}
        paired = Counter()
        by_pair = {r["pair_id"]: {} for r in rows}
        for r in rows: by_pair[r["pair_id"]][r["arm"]] = bool(r["final_success"])
        for vals in by_pair.values(): paired["_".join(f"{a}={int(vals.get(a, False))}" for a in ARMS)] += 1
        summary = {"record_type": "visual_feedback_paired_summary", "completed_at_utc": datetime.now(timezone.utc).isoformat(), "source": args.source, "manifest": args.manifest, "points": len(selected), "paired_records": len(rows), "arms": list(ARMS), "by_arm": by_arm, "paired_final_success": dict(paired), "protocol": "same frozen failure frame; sparse structured state; image is the only intended additional input", "elapsed_s": time.time() - started, "output": str(output)}
        handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
