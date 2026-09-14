from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import GoalSpec
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.collector import world_state_from_task
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.repair import PlanRepairer
from ch3.logger.episode_logger import EpisodeLogger


def state_from_record(task: dict[str, Any], record: dict[str, Any]) -> WorldState:
    state = WorldState.table_scene(task["objects"])
    state.at.update(record.get("at", {}))
    state.holding.update(record.get("holding", {}))
    state.pushed = set(record.get("pushed", []))
    state.pressed = set(record.get("pressed", []))
    return state


def select_cases(episodes: list[dict[str, Any]], counts: dict[str, int]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for family, wanted in counts.items():
        rows = [e for e in episodes if e.get("task_family") == family and str(e.get("final_reject_reason", "")).startswith("symbolic_validation")]
        rows.sort(key=lambda e: (e["task_id"], e["seed"]))
        if len(rows) < wanted:
            raise RuntimeError(f"not enough visual failures for {family}: {len(rows)} < {wanted}")
        selected.extend(rows[:wanted])
    selected.sort(key=lambda e: (e["task_family"], e["task_id"], e["seed"]))
    return selected


def repair_once(*, repairer: PlanRepairer, validator: Validator, task: dict[str, Any], episode: dict[str, Any], visual: bool, temperature: float) -> dict[str, Any]:
    task_id = episode["task_id"]
    seed = int(episode["seed"])
    source_task = tasks_by_id[task_id]
    previous = episode["attempts"][0]
    failed_plan = ModelPlan.model_validate(previous["plan"])
    current_state = state_from_record(source_task, previous["symbolic_state"])
    validation = ValidationResult(
        valid=False,
        first_invalid_step=1,
        error_code=None,
        message="metaworld_execution_failure",
        layer="execution",
        validated_prefix=[],
        final_state=current_state,
    )
    generation = PlanGeneration(
        failed_plan,
        VLMResponse(model="source", content="", finish_reason="stop", latency_ms=0, prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )
    repair_task = dict(source_task)
    if visual:
        failure_frame = previous.get("failure_frame")
        if not failure_frame or not Path(failure_frame).is_file():
            raise RuntimeError(f"missing failure frame for {task_id} seed={seed}")
        repair_task["image_path"] = failure_frame
        repair_task["visual_feedback"] = True

    started = time.time()
    repair = repairer.repair(
        repair_mode="R1_FROM_STATE",
        task=repair_task,
        initial_generation=generation,
        validation=validation,
        initial_state=world_state_from_task(source_task),
        seed=seed,
        temperature=temperature,
    )
    elapsed = time.time() - started
    validator.scene_objects = set(source_task["objects"])
    validation_result = validator.validate(repair.plan, world_state_from_task(source_task)) if repair.plan is not None else None
    plan = repair.plan.model_dump(mode="json") if repair.plan is not None else None
    return {
        "record_type": "visual_prompt_bias_pilot_case",
        "task_id": task_id,
        "source_task_id": source_task["task_id"],
        "task_family": source_task.get("task_family"),
        "seed": seed,
        "arm": "VISUAL_PROMPT_V2" if visual else "STATE_PROMPT_V2",
        "source_visual_arm_failed": True,
        "repair_mode": "R1_FROM_STATE",
        "accepted": repair.accepted,
        "reject_reason": repair.reject_reason,
        "parse_error": repair.parse_error,
        "valid": bool(validation_result.valid) if validation_result is not None else False,
        "goal_satisfied": bool(goal_satisfied(validation_result.final_state, GoalSpec.model_validate(source_task["goal"]), {"left", "right"})) if validation_result is not None and validation_result.valid else False,
        "error_code": str(validation_result.error_code) if validation_result is not None and validation_result.error_code is not None else None,
        "error_layer": getattr(validation_result, "layer", None) if validation_result is not None else None,
        "error_message": validation_result.message if validation_result is not None else "parse_error",
        "plan": plan,
        "elapsed_s": elapsed,
        "response": {
            "model": repair.response.model,
            "prompt_tokens": repair.response.prompt_tokens,
            "completion_tokens": repair.response.completion_tokens,
            "total_tokens": repair.response.total_tokens,
            "finish_reason": repair.response.finish_reason,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--env-file", default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env")
    parser.add_argument("--source", default="data/collections/sim_closed_loop_visualstate_perturbed100_20260914_115417.json")
    parser.add_argument("--tasks", default="data/scenarios/multiskill_tasks_v1.jsonl")
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()

    global tasks_by_id
    tasks_by_id = {t["task_id"]: t for t in map(json.loads, Path(args.tasks).open(encoding="utf-8"))}
    source = json.load(open(args.source, encoding="utf-8"))
    episodes = source["episode_detail"]
    counts = {"push": 5, "press": 3, "pick_place": 2}
    cases = select_cases(episodes, counts)
    print(json.dumps({"selected_cases": [{"task_id": e["task_id"], "seed": e["seed"], "family": e["task_family"]} for e in cases]}, ensure_ascii=False), flush=True)

    client = DashScopeVLMClient(model=args.model, env_path=args.env_file)
    repairer = PlanRepairer(client, max_tokens=args.max_tokens, json_mode=True)
    base_validator = Validator(scene_objects=set(), registry=CapabilityRegistry.from_yaml(args.registry))
    logger = EpisodeLogger(args.output)
    started = time.time()
    for index, episode in enumerate(cases, 1):
        for visual in (False, True):
            print(f"[visual-prompt-pilot] case={index}/{len(cases)} arm={'VISUAL' if visual else 'STATE'} task={episode['task_id']} seed={episode['seed']} start", flush=True)
            record = repair_once(
                repairer=repairer,
                validator=base_validator,
                task=None,
                episode=episode,
                visual=visual,
                temperature=args.temperature,
            )
            logger.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)

    records = logger.read_all()
    by_arm: dict[str, Counter[str]] = {}
    for record in records:
        arm = record["arm"]
        stats = by_arm.setdefault(arm, Counter())
        stats["cases"] += 1
        stats["valid"] += int(record["valid"])
        stats["parse_error"] += int(record["parse_error"] is not None)
        stats["schema_error"] += int((record.get("error_code") or "") == "SCHEMA_ERROR")
    summary = {
        "record_type": "visual_prompt_bias_pilot_summary",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "selected_cases": len(cases),
        "vlm_calls": len(records),
        "prompt_version": "repair_prompt_v2_dynamic_example",
        "repair_mode": "R1_FROM_STATE",
        "elapsed_s": time.time() - started,
        "summary_by_arm": {k: dict(v) for k, v in by_arm.items()},
        "output": args.output,
    }
    logger.append(summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
