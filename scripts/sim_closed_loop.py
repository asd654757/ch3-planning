#!/usr/bin/env python3
"""Online state-feedback closed-loop simulation pilot.

This pilot is deliberately narrower than a visual closed-loop benchmark:
the VLM plans at task level, the deterministic validator admits/rejects the
plan, MetaWorld executes a homogeneous primitive segment, and a failed
segment triggers state-aware repair.  The current MetaWorld adapter has one
movable puck per family episode, so each homogeneous segment runs in a fresh
episode and execution feedback is mapped back to symbolic facts.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("MUJOCO_GL", "egl")

import re

from ch3.capability.registry import CapabilityRegistry
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.logger.episode_logger import EpisodeLogger
from ch3.schema.model_plan import ModelPlan, ModelPlanAction
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.collector import world_state_from_task
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.planner import InitialPlanner, PlanGeneration
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.repair import PlanRepairer
from ch3.validator.result import ValidationResult
from scripts.sim_compare_baselines import (
    execute_multiskill_plan,
    is_executable_multiskill,
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"no tasks loaded from {path}")
    return rows


def segment_boundaries(plan: ModelPlan) -> list[tuple[int, int]]:
    if all(a.skill.value in {"pick", "place"} for a in plan.actions):
        return [(i, min(i + 2, len(plan.actions))) for i in range(0, len(plan.actions), 2)]
    return [(0, len(plan.actions))]


def apply_segment(state: WorldState, segment: list[ModelPlanAction], success: bool) -> None:
    """Apply a segment's declared effect only when the whole segment succeeds."""
    if not success:
        return
    skills = [a.skill.value for a in segment]
    if skills == ["pick", "place"] and len(segment) == 2:
        state.at[segment[0].object_id] = str(segment[1].target_id)
        state.holding.pop(segment[0].arm.value, None)
    elif skills == ["push"]:
        state.at[segment[0].object_id] = str(segment[0].target_id)
        state.pushed.add(segment[0].object_id)
    elif skills == ["press"]:
        state.pressed.add(segment[0].object_id)


def symbolic_state_record(state: WorldState) -> dict[str, Any]:
    return {
        "at": dict(sorted(state.at.items())),
        "holding": dict(sorted(state.holding.items())),
        "pushed": sorted(state.pushed),
        "pressed": sorted(state.pressed),
        "facts": sorted(state.facts() | state.empty_hand_facts({"left", "right"})),
    }


def execution_feedback_validation(
    *,
    failed_plan: ModelPlan,
    completed_prefix: list[ModelPlanAction],
    current_state: WorldState,
    message: str,
) -> ValidationResult:
    """Build a validator-compatible execution-failure localization result."""
    return ValidationResult(
        valid=False,
        first_invalid_step=(completed_prefix[-1].step_id + 1 if completed_prefix else 1),
        error_code=None,
        message=message,
        layer="execution",
        validated_prefix=completed_prefix,
        final_state=current_state,
    )


def run_closed_loop_episode(
    *,
    task: dict[str, Any],
    seed: int,
    planner: InitialPlanner,
    repairer: PlanRepairer,
    validator: Validator,
    registry: CapabilityRegistry,
    max_replan_rounds: int,
    max_steps: int,
    observation_size: int,
    temperature: float,
    repair_temperature: float,
    visual_feedback: bool = False,
    feedback_frames_dir=None,
) -> dict[str, Any]:
    task_id = str(task["task_id"])
    vlm_calls = 0
    attempts: list[dict[str, Any]] = []
    final_success = False
    final_reject_reason: str | None = None

    for round_index in range(max_replan_rounds + 1):
        if round_index == 0:
            generation: PlanGeneration | Any = planner.plan(
                task, seed=seed, temperature=temperature
            )
            vlm_calls += 1
            label = "INITIAL"
        else:
            prev = attempts[-1]
            completed_prefix = prev.get("completed_prefix_actions", [])
            current_state = prev["symbolic_state"].copy()
            # Execution segments are the atomic interface units in this pilot.
            # A partially completed pick/place pair is treated as failed, so a
            # fresh episode does not silently assume simulator-internal grasp
            # state that the adapter cannot restore.
            feedback = execution_feedback_validation(
                failed_plan=prev["plan"],
                completed_prefix=completed_prefix,
                current_state=current_state,
                message="metaworld_execution_failure",
            )
            repair_mode = (
                "R1_FROM_STATE"
                if feedback.final_state is not None and feedback.validated_prefix
                else "R1"
            )
            # A live execution frame is passed through the existing image
            # interface.  The repairer still receives deterministic state
            # facts, so this is visual-state feedback rather than vision-only
            # state estimation.
            repair_task = task
            if visual_feedback and prev.get("failure_frame"):
                repair_task = {
                    **task,
                    "image_path": prev["failure_frame"],
                    "visual_feedback": True,
                }
            generation = repairer.repair(
                repair_mode=repair_mode,
                task=repair_task,
                initial_generation=prev["generation"],
                validation=feedback,
                initial_state=world_state_from_task(task),
                seed=seed + round_index,
                temperature=repair_temperature,
            )
            vlm_calls += 1
            label = repair_mode

        plan = generation.plan
        attempt: dict[str, Any] = {
            "round": round_index,
            "label": label,
            "plan": plan,
            "generation": generation,
            "parse_error": getattr(generation, "parse_error", None),
            "infeasible_reason": getattr(generation, "infeasible_reason", None),
            "repair_mode": getattr(generation, "repair_mode", None),
            "accepted": getattr(generation, "accepted", None),
            "reject_reason": getattr(generation, "reject_reason", None),
            "vlm_calls": 1,
        }
        if plan is None:
            final_reject_reason = (
                getattr(generation, "parse_error", None)
                or getattr(generation, "infeasible_reason", None)
                or getattr(generation, "reject_reason", None)
                or "plan_parse_or_generation_failure"
            )
            attempt["failure_stage"] = "model_plan"
            attempts.append(attempt)
            break

        validation = validator.validate(plan, world_state_from_task(task))
        attempt["symbolic_valid"] = validation.valid
        attempt["error_code"] = validation.error_code.value if validation.error_code else None
        attempt["error_layer"] = validation.layer
        attempt["error_message"] = validation.message
        if not validation.valid:
            final_reject_reason = f"symbolic_validation:{validation.error_code}"
            attempt["failure_stage"] = "symbolic_validation"
            attempts.append(attempt)
            break

        if not is_executable_multiskill(plan.actions):
            final_reject_reason = "not_homogeneous_executable_plan"
            attempt["failure_stage"] = "execution_interface"
            attempts.append(attempt)
            break

        segments = segment_boundaries(plan)
        sim_success, segment_results, sim_tasks = execute_multiskill_plan(
            plan,
            registry,
            (task_id, seed, f"round_{round_index}"),
            "ROUTED_CLOSED_LOOP",
            max_steps=max_steps,
            observation_size=observation_size,
            capture_failure_frame_dir=feedback_frames_dir if visual_feedback else None,
        )
        attempt["segments"] = segment_results
        attempt["sim_tasks"] = sim_tasks
        attempt["sim_success"] = sim_success
        failure_frame = next(
            (
                item.get("failure_frame")
                for item in reversed(segment_results)
                if item.get("failure_frame")
            ),
            None,
        )
        attempt["failure_frame"] = failure_frame

        symbolic_state = world_state_from_task(task)
        completed_prefix: list[ModelPlanAction] = []
        for segment_result, (start, end) in zip(segment_results, segments):
            segment_success = bool(segment_result.get("success"))
            segment = plan.actions[start:end]
            apply_segment(symbolic_state, segment, segment_success)
            if segment_success:
                completed_prefix.extend(segment)
            else:
                break
        attempt["symbolic_state"] = symbolic_state_record(symbolic_state)
        attempt["completed_prefix_actions"] = [
            action.model_copy(deep=True) for action in completed_prefix
        ]

        if sim_success:
            final_success = True
            final_reject_reason = None
            attempts.append(attempt)
            break

        final_reject_reason = "metaworld_execution_failure"
        attempt["failure_stage"] = "metaworld_execution"
        attempts.append(attempt)

    # ModelPlanAction objects are needed across replan rounds but must be
    # serialized in the episode record.  Convert them only after the loop.
    for attempt in attempts:
        if "generation" in attempt:
            generation = attempt["generation"]
            response = getattr(generation, "response", None)
            attempt["generation"] = {
                "repair_mode": getattr(generation, "repair_mode", None),
                "accepted": getattr(generation, "accepted", None),
                "reject_reason": getattr(generation, "reject_reason", None),
                "parse_error": getattr(generation, "parse_error", None),
                "infeasible_reason": getattr(generation, "infeasible_reason", None),
                "prompt_id": getattr(generation, "prompt_id", ""),
                "prompt_hash": getattr(generation, "prompt_hash", ""),
                "response": {
                    "model": getattr(response, "model", None),
                    "latency_ms": getattr(response, "latency_ms", None),
                    "prompt_tokens": getattr(response, "prompt_tokens", 0),
                    "completion_tokens": getattr(response, "completion_tokens", 0),
                    "total_tokens": getattr(response, "total_tokens", 0),
                    "finish_reason": getattr(response, "finish_reason", None),
                },
            }
        if "plan" in attempt:
            attempt["plan"] = attempt["plan"].model_dump(mode="json")
        if "completed_prefix_actions" in attempt:
            attempt["completed_prefix_actions"] = [
                action.model_dump(mode="json")
                for action in attempt["completed_prefix_actions"]
            ]

    return {
        "record_type": "sim_closed_loop_episode",
        "task_id": task_id,
        "task_family": task.get("task_family"),
        "seed": seed,
        "final_success": final_success,
        "final_reject_reason": final_reject_reason,
        "vlm_calls": vlm_calls,
        "replan_rounds": max(0, len(attempts) - 1),
        "attempts": attempts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=Path("data/scenarios/multiskill_tasks_v1.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--env-file",
        default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--client", choices=("real", "mock"), default="real")
    parser.add_argument(
        "--task-families",
        nargs="+",
        choices=("pick_place", "push", "press"),
        default=("pick_place", "push", "press"),
    )
    parser.add_argument(
        "--episodes-per-family",
        type=int,
        default=4,
        help="Fresh task x seed episodes selected per family.",
    )
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--max-replan-rounds", type=int, default=1)
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument(
        "--visual-feedback",
        action="store_true",
        help="Send the failed segment's rendered frame to the repair VLM.",
    )
    parser.add_argument(
        "--feedback-frames-dir",
        type=Path,
        default=None,
        help="Directory for failed-segment feedback frames.",
    )
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--repair-temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()
    if args.episodes_per_family < 1:
        raise ValueError("--episodes-per-family must be >= 1")
    if args.max_replan_rounds < 0:
        raise ValueError("--max-replan-rounds must be >= 0")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.visual_feedback:
        if args.feedback_frames_dir is None:
            args.feedback_frames_dir = args.output.with_suffix(".frames")
        args.feedback_frames_dir.mkdir(parents=True, exist_ok=True)

    tasks = load_jsonl(args.tasks)
    selected: list[tuple[dict[str, Any], int]] = []
    for family in args.task_families:
        family_tasks = [t for t in tasks if t.get("task_family") == family]
        if len(family_tasks) < args.episodes_per_family:
            raise RuntimeError(
                f"family {family} has only {len(family_tasks)} tasks, "
                f"want {args.episodes_per_family}"
            )
        for offset, task in enumerate(family_tasks[: args.episodes_per_family]):
            selected.append((task, args.seed_offset + offset))

    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    if args.client == "mock":
        # The mock is only for pipeline smoke tests.  It reconstructs the
        # simplest family-specific valid plan from the rendered prompt.
        def mock_valid_plan(payload: dict[str, Any]) -> str | dict[str, Any]:
            prompt = payload["messages"][1]["content"]
            match = re.search(r"Visible/closed-world scene object IDs:\s*(\[.*?\])", prompt, re.S)
            objects = json.loads(match.group(1)) if match else []
            if not objects:
                return {"actions": []}
            if "to push" in prompt:
                action = [{
                    "step_id": 1, "skill": "push",
                    "object_id": objects[0], "target_id": "goal_pad",
                    "arm": "right",
                }]
            elif "to press" in prompt:
                action = [{
                    "step_id": 1, "skill": "press",
                    "object_id": objects[0], "arm": "right",
                }]
            else:
                action = [
                    {
                        "step_id": 1, "skill": "pick",
                        "object_id": objects[0], "arm": "right",
                    },
                    {
                        "step_id": 2, "skill": "place",
                        "object_id": objects[0],
                        "target_id": objects[1] if len(objects) > 1 else "table",
                        "arm": "right",
                    },
                ]
            return {"actions": action}

        client = MockVLMClient(response=mock_valid_plan)
    else:
        kwargs: dict[str, Any] = {"model": args.model}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        if args.env_file:
            kwargs["env_path"] = args.env_file
        if args.api_key:
            kwargs["api_key"] = args.api_key
        client = DashScopeVLMClient(**kwargs)
    planner = InitialPlanner(client, max_tokens=args.max_tokens)
    repairer = PlanRepairer(client, max_tokens=args.max_tokens)
    logger = EpisodeLogger(args.output.with_suffix(".jsonl"))

    episodes: list[dict[str, Any]] = []
    started = time.time()
    for episode_index, (task, seed) in enumerate(selected, 1):
        validator.scene_objects = set(task["objects"])
        print(
            f"[sim-closed-loop] episode={episode_index}/{len(selected)} "
            f"task={task['task_id']} family={task['task_family']} seed={seed} start",
            flush=True,
        )
        record = run_closed_loop_episode(
            task=task,
            seed=seed,
            planner=planner,
            repairer=repairer,
            validator=validator,
            registry=registry,
            max_replan_rounds=args.max_replan_rounds,
            max_steps=args.max_steps_per_primitive,
            observation_size=args.observation_size,
            visual_feedback=args.visual_feedback,
            feedback_frames_dir=args.feedback_frames_dir,
            temperature=args.temperature,
            repair_temperature=args.repair_temperature,
        )
        record["episode_index"] = episode_index
        episodes.append(record)
        logger.append(record)
        print(
            f"[sim-closed-loop] episode={episode_index}/{len(selected)} "
            f"success={record['final_success']} vlm_calls={record['vlm_calls']} "
            f"replans={record['replan_rounds']} reason={record['final_reject_reason']}",
            flush=True,
        )

    family_summary: dict[str, dict[str, Any]] = {}
    for family in args.task_families:
        rows = [r for r in episodes if r["task_family"] == family]
        family_summary[family] = {
            "episodes": len(rows),
            "successes": sum(r["final_success"] for r in rows),
            "success_rate": sum(r["final_success"] for r in rows) / len(rows),
            "vlm_calls": sum(r["vlm_calls"] for r in rows),
            "replan_rounds": sum(r["replan_rounds"] for r in rows),
        }
    summary = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_type": "sim_closed_loop_summary",
        "benchmark": "sim_closed_loop_pilot_v1",
        "episodes": len(episodes),
        "successes": sum(r["final_success"] for r in episodes),
        "success_rate": sum(r["final_success"] for r in episodes) / len(episodes),
        "vlm_calls": sum(r["vlm_calls"] for r in episodes),
        "replan_rounds": sum(r["replan_rounds"] for r in episodes),
        "summary_by_family": family_summary,
        "visual_feedback": args.visual_feedback,
        "execution_protocol": (
            "VLM task plan -> deterministic validator -> homogeneous MetaWorld "
            "segment -> state feedback"
            + (" + current rendered frame -> " if args.visual_feedback else " -> ")
            + "R1/R1_FROM_STATE repair"
        ),
        "limitation": (
            (
                "Online visual-state feedback closed loop; symbolic state is "
                "still provided by the simulator adapter, so this is not a "
                "vision-only state-estimation benchmark. Homogeneous segments "
                "use fresh episodes under the current MetaWorld adapter."
            )
            if args.visual_feedback
            else (
                "State-feedback closed loop, not visual closed loop; "
                "homogeneous segments use fresh episodes under the current "
                "MetaWorld adapter."
            )
        ),
        "elapsed_s": round(time.time() - started, 3),
        "output": str(args.output),
        "episode_detail": episodes,
    }
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "episode_detail"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
