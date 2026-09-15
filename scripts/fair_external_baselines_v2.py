#!/usr/bin/env python3
"""Fair external baselines on the frozen 100-point perturbed closed loop.

This runner gives Self-Refine and Checker-loop the same structured-state
protocol and goal-derived action skeleton used by prompt-v2 repair, while
preserving their method definitions:

- SELF_REFINE_STATE_V2: no deterministic validator feedback.
- CHECKER_LOOP_STATE_V2: deterministic checker feedback, up to two calls.

Final plans are evaluated by the same Validator/Goal Checker and executed in
the same frozen-failure repair-then-MetaWorld protocol as STATE_PROMPT_V2.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

os_environ_set = False
try:
    import os

    os.environ.setdefault("MUJOCO_GL", "egl")
    os_environ_set = True
except ImportError:  # pragma: no cover
    pass

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.logger.episode_logger import EpisodeLogger
from ch3.repair.prefix_guard import merge_locked_prefix  # noqa: F401
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.collector import world_state_from_task
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.parser import PlanParseError, parse_structured_plan_or_infeasible
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.repair import goal_action_skeleton, required_transports
from scripts.sim_compare_baselines import execute_multiskill_plan, is_executable_multiskill


BASELINES = ("SELF_REFINE_STATE_V2", "CHECKER_LOOP_STATE_V2")
SCHEMA_VERSION = "2026-09-15-fair-external-baseline-state-v2"
PROMPT_VERSION = "fair_external_state_v2_goal_skeleton"


def state_from_record(task: dict[str, Any], record: dict[str, Any]) -> WorldState:
    state = WorldState.table_scene(task["objects"])
    state.at.update(record.get("at", {}))
    state.holding.update(record.get("holding", {}))
    state.pushed = set(record.get("pushed", []))
    state.pressed = set(record.get("pressed", []))
    return state


def build_execution_failure_validation(
    source_task: dict[str, Any], episode: dict[str, Any]
) -> ValidationResult:
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


def state_facts(state: WorldState) -> list[str]:
    return sorted(state.facts() | state.empty_hand_facts({"left", "right"}))


def plan_evaluation(
    plan: Optional[ModelPlan],
    *,
    task: dict[str, Any],
    validator: Validator,
    state: WorldState,
) -> tuple[Optional[ValidationResult], bool]:
    if plan is None:
        return None, False
    validator.scene_objects = set(task["objects"])
    validation = validator.validate(plan, state)
    goal_ok = False
    if validation.valid:
        goal_ok = bool(
            goal_satisfied(
                validation.final_state,
                GoalSpec.model_validate(task["goal"]),
                {"left", "right"},
            )
        )
    return validation, goal_ok


def parse_generation(
    response: VLMResponse,
    *,
    prompt: str,
    prompt_id: str,
    prompt_hash: str,
) -> PlanGeneration:
    try:
        plan, infeasible_reason = parse_structured_plan_or_infeasible(response.content)
        return PlanGeneration(
            plan,
            response,
            None,
            prompt,
            prompt_id,
            prompt_hash,
            infeasible_reason=infeasible_reason,
        )
    except PlanParseError as exc:
        return PlanGeneration(
            None,
            response,
            str(exc),
            prompt,
            prompt_id,
            prompt_hash,
        )


def common_prompt_values(
    *,
    task: dict[str, Any],
    state: WorldState,
    plan: ModelPlan,
) -> dict[str, Any]:
    return {
        "instruction": task["instruction"],
        "objects": sorted(task["objects"]),
        "goal": task["goal"],
        "current_state": state_facts(state),
        "goal_action_skeleton": goal_action_skeleton(task),
        "current_plan": plan.model_dump(),
        "failed_plan": plan.model_dump(),
    }


def checker_feedback(
    *,
    round_number: int,
    validation: Optional[ValidationResult],
    parse_error: Optional[str],
    goal_ok: bool,
) -> dict[str, Any]:
    if parse_error is not None:
        return {
            "round": round_number,
            "valid": False,
            "goal_satisfied": False,
            "first_invalid_step": None,
            "error_code": "SCHEMA_ERROR",
            "error_layer": "syntax",
            "error_message": parse_error,
            "validated_prefix_step_ids": [],
        }
    return {
        "round": round_number,
        "valid": bool(validation is not None and validation.valid),
        "goal_satisfied": goal_ok,
        "first_invalid_step": getattr(validation, "first_invalid_step", None)
        if validation is not None
        else None,
        "error_code": validation.error_code.value
        if validation is not None and validation.error_code
        else None,
        "error_layer": getattr(validation, "layer", None)
        if validation is not None
        else None,
        "error_message": getattr(validation, "message", "")
        if validation is not None
        else "",
        "validated_prefix_step_ids": [
            action.step_id
            for action in getattr(validation, "validated_prefix", [])
        ]
        if validation is not None
        else [],
    }


def run_self_refine(
    *,
    task: dict[str, Any],
    state: WorldState,
    failed_plan: ModelPlan,
    client: Any,
    prompts: PromptLibrary,
    seed: int,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> PlanGeneration:
    values = common_prompt_values(task=task, state=state, plan=failed_plan)
    prompt, prompt_id, prompt_hash = prompts.render(
        "external_self_refine_state_v2", **values
    )
    response = client.complete(
        system_prompt=(
            "You are a robotics planner performing self-refinement. Output only "
            "one JSON object. Do not add commentary or Markdown."
        ),
        user_prompt=prompt,
        image_path=None,
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        json_mode=json_mode,
    )
    generation = parse_generation(
        response,
        prompt=prompt,
        prompt_id=prompt_id,
        prompt_hash=prompt_hash,
    )
    generation.__dict__["baseline_rounds"] = 1
    generation.__dict__["baseline_total_tokens"] = response.total_tokens
    generation.__dict__["baseline_total_latency_ms"] = response.latency_ms
    return generation


def run_checker_loop(
    *,
    task: dict[str, Any],
    state: WorldState,
    failed_plan: ModelPlan,
    initial_validation: ValidationResult,
    client: Any,
    prompts: PromptLibrary,
    validator: Validator,
    seed: int,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    max_rounds: int = 2,
) -> PlanGeneration:
    current_plan = failed_plan
    current_validation: Optional[ValidationResult] = initial_validation
    current_parse_error: Optional[str] = None
    current_goal_ok = False
    generation: Optional[PlanGeneration] = None
    total_tokens = 0
    total_latency_ms = 0

    goal_facts = [
        fact for fact in task.get("goal", {}).get("facts", []) if isinstance(fact, str)
    ]
    for round_number in range(1, max_rounds + 1):
        values = common_prompt_values(task=task, state=state, plan=current_plan)
        values.update(
            {
                "remaining_goal_facts": goal_facts,
                "required_transports": required_transports(task, state),
                "release_actions": [],
                "checker_feedback": checker_feedback(
                    round_number=round_number,
                    validation=current_validation,
                    parse_error=current_parse_error,
                    goal_ok=current_goal_ok,
                ),
            }
        )
        prompt, prompt_id, prompt_hash = prompts.render(
            "external_checker_loop_state_v2", **values
        )
        response = client.complete(
            system_prompt=(
                "You are a robotics plan repairer in a checker loop. Output only "
                "one JSON object. Do not add commentary or Markdown."
            ),
            user_prompt=prompt,
            image_path=None,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            json_mode=json_mode,
        )
        total_tokens += response.total_tokens
        total_latency_ms += response.latency_ms
        returned_plan: Optional[ModelPlan] = None
        try:
            returned_plan, infeasible_reason = parse_structured_plan_or_infeasible(
                response.content
            )
            current_parse_error = None
            generation = PlanGeneration(
                returned_plan,
                response,
                None,
                prompt,
                prompt_id,
                prompt_hash,
                infeasible_reason=infeasible_reason,
            )
        except PlanParseError as exc:
            current_parse_error = str(exc)
            generation = PlanGeneration(
                None,
                response,
                current_parse_error,
                prompt,
                prompt_id,
                prompt_hash,
            )

        if generation.infeasible:
            break
        if returned_plan is not None:
            current_plan = returned_plan
            current_validation, current_goal_ok = plan_evaluation(
                returned_plan, task=task, validator=validator, state=state
            )
            if current_validation is not None and current_validation.valid and current_goal_ok:
                break
        else:
            current_validation = None
            current_goal_ok = False

    assert generation is not None
    generation.__dict__["baseline_rounds"] = round_number
    generation.__dict__["baseline_total_tokens"] = total_tokens
    generation.__dict__["baseline_total_latency_ms"] = total_latency_ms
    return generation


def baseline_case(
    *,
    source_task: dict[str, Any],
    episode: dict[str, Any],
    baseline: str,
    client: Any,
    prompts: PromptLibrary,
    validator: Validator,
    registry: CapabilityRegistry,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    checker_rounds: int,
    max_steps: int,
    observation_size: int,
) -> dict[str, Any]:
    task_id = str(episode["task_id"])
    seed = int(episode["seed"])
    previous = episode["attempts"][0]
    failed_plan = ModelPlan.model_validate(previous["plan"])
    current_state = state_from_record(source_task, previous["symbolic_state"])
    initial_validation = build_execution_failure_validation(source_task, episode)

    started = time.time()
    if baseline == "SELF_REFINE_STATE_V2":
        generation = run_self_refine(
            task=source_task,
            state=current_state,
            failed_plan=failed_plan,
            client=client,
            prompts=prompts,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
    elif baseline == "CHECKER_LOOP_STATE_V2":
        generation = run_checker_loop(
            task=source_task,
            state=current_state,
            failed_plan=failed_plan,
            initial_validation=initial_validation,
            client=client,
            prompts=prompts,
            validator=validator,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            max_rounds=checker_rounds,
        )
    else:
        raise ValueError(f"unsupported baseline: {baseline}")
    baseline_elapsed = time.time() - started

    validation, goal_ok = plan_evaluation(
        generation.plan,
        task=source_task,
        validator=validator,
        state=current_state,
    )
    sim_attempted = False
    sim_success = False
    sim_tasks: list[str] = []
    primitive_results: list[dict[str, Any]] = []
    if (
        generation.plan is not None
        and validation is not None
        and validation.valid
        and goal_ok
        and is_executable_multiskill(generation.plan.actions)
    ):
        sim_attempted = True
        sim_started = time.time()
        sim_success, primitive_results, sim_tasks = execute_multiskill_plan(
            generation.plan,
            registry,
            (task_id, seed, baseline),
            baseline,
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
    final_response = generation.response
    return {
        "record_type": "fair_external_baseline_v2_case",
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "task_family": source_task.get("task_family"),
        "seed": seed,
        "baseline": baseline,
        "source_arm": "VISUAL_PROMPT_V1",
        "source_final_success": bool(episode.get("final_success")),
        "prompt_version": PROMPT_VERSION,
        "state_visibility": "full_structured_state",
        "goal_action_skeleton": True,
        "deterministic_checker_feedback": baseline == "CHECKER_LOOP_STATE_V2",
        "rounds": int(generation.__dict__.get("baseline_rounds", 1)),
        "accepted": not generation.infeasible and generation.plan is not None,
        "reject_reason": generation.infeasible_reason,
        "parse_error": generation.parse_error,
        "symbolic_valid": bool(validation is not None and validation.valid),
        "goal_satisfied": goal_ok,
        "error_code": validation.error_code.name
        if validation is not None and validation.error_code
        else None,
        "error_layer": validation.layer if validation is not None else None,
        "error_message": validation.message
        if validation is not None
        else generation.parse_error or "infeasible_or_parse_error",
        "plan": generation.plan.model_dump(mode="json") if generation.plan else None,
        "sim_attempted": sim_attempted,
        "sim_success": bool(sim_success),
        "final_success": bool(
            validation is not None
            and validation.valid
            and goal_ok
            and sim_success
        ),
        "sim_tasks": sim_tasks,
        "primitive_results": compact_primitives,
        "baseline_elapsed_s": baseline_elapsed,
        "sim_elapsed_s": sim_elapsed,
        "prompt_id": generation.prompt_id,
        "prompt_hash": generation.prompt_hash,
        "response": {
            "model": final_response.model,
            "prompt_tokens": final_response.prompt_tokens,
            "completion_tokens": final_response.completion_tokens,
            "total_tokens": final_response.total_tokens,
            "finish_reason": final_response.finish_reason,
        },
        "cost": {
            "rounds": int(generation.__dict__.get("baseline_rounds", 1)),
            "prompt_tokens": final_response.prompt_tokens,
            "completion_tokens": final_response.completion_tokens,
            "total_tokens": int(generation.__dict__.get("baseline_total_tokens", 0)),
            "latency_ms": int(
                generation.__dict__.get("baseline_total_latency_ms", 0)
            ),
        },
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_baseline: dict[str, dict[str, Any]] = {}
    by_family: dict[str, dict[str, Any]] = defaultdict(dict)
    paired: Counter[str] = Counter()
    records_by_point = {
        (r["task_id"], r["seed"], r["baseline"]): r for r in records
    }
    for baseline in BASELINES:
        rows = [r for r in records if r["baseline"] == baseline]
        stats: dict[str, Any] = {
            "points": len(rows),
            "rounds": sum(r["rounds"] for r in rows),
            "symbolic_valid": sum(r["symbolic_valid"] for r in rows),
            "goal_satisfied": sum(r["goal_satisfied"] for r in rows),
            "sim_attempted": sum(r["sim_attempted"] for r in rows),
            "sim_success": sum(r["sim_success"] for r in rows),
            "final_success": sum(r["final_success"] for r in rows),
            "parse_error": sum(r["parse_error"] is not None for r in rows),
            "schema_error": sum((r.get("error_code") or "") == "SCHEMA_ERROR" for r in rows),
            "prompt_tokens": sum(r["cost"]["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["cost"]["completion_tokens"] for r in rows),
            "total_tokens": sum(r["cost"]["total_tokens"] for r in rows),
        }
        stats["final_success_rate"] = (
            stats["final_success"] / stats["points"] if stats["points"] else None
        )
        stats["avg_rounds"] = stats["rounds"] / stats["points"] if stats["points"] else None
        by_baseline[baseline] = stats
        for row in rows:
            family = row["task_family"]
            by_family[family].setdefault(baseline, Counter())
            by_family[family][baseline]["points"] += 1
            by_family[family][baseline]["final_success"] += int(row["final_success"])

    serial_by_family: dict[str, dict[str, Any]] = {}
    for family, arms in sorted(by_family.items()):
        serial_by_family[family] = {
            baseline: {
                "points": counts["points"],
                "final_success": counts["final_success"],
                "final_success_rate": counts["final_success"] / counts["points"]
                if counts["points"]
                else None,
            }
            for baseline, counts in arms.items()
        }

    keys = {
        (r["task_id"], r["seed"]) for r in records
    }
    for task_id, seed in keys:
        self_row = records_by_point.get((task_id, seed, "SELF_REFINE_STATE_V2"))
        checker_row = records_by_point.get((task_id, seed, "CHECKER_LOOP_STATE_V2"))
        if self_row is None or checker_row is None:
            continue
        outcomes = {
            "SELF_REFINE_STATE_V2": self_row["final_success"],
            "CHECKER_LOOP_STATE_V2": checker_row["final_success"],
        }
        paired[
            "_".join(
                baseline if success else "not_" + baseline
                for baseline, success in outcomes.items()
            )
        ] += 1
    return {
        "by_baseline": by_baseline,
        "by_family": serial_by_family,
        "paired_final_success": dict(paired),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument(
        "--env-file",
        default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env",
    )
    parser.add_argument(
        "--source",
        default="data/collections/sim_closed_loop_visualstate_perturbed100_20260914_115417.json",
    )
    parser.add_argument(
        "--reference-state-arm",
        default="data/collections/visual_prompt_v2_repair_rerun_20260914_210737.jsonl",
    )
    parser.add_argument("--tasks", default="data/scenarios/multiskill_tasks_v1.jsonl")
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 means all 100 points")
    parser.add_argument("--baselines", nargs="+", choices=BASELINES, default=list(BASELINES))
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--checker-rounds", type=int, default=2)
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--client", choices=["real", "mock"], default="real")
    parser.add_argument("--resume", action="store_true")
    return parser


def done_keys(path: Path) -> set[tuple[str, int, str]]:
    if not path.exists():
        return set()
    keys: set[tuple[str, int, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("record_type") == "fair_external_baseline_v2_case":
            keys.add((str(row["task_id"]), int(row["seed"]), str(row["baseline"])))
    return keys


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.limit < 0 or args.checker_rounds < 1:
        raise ValueError("--limit must be >= 0 and --checker-rounds must be >= 1")
    output_path = Path(args.output)
    if output_path.exists() and not args.resume:
        raise FileExistsError(
            f"Refusing to overwrite existing output: {output_path}. "
            "Use a new timestamped output or --resume."
        )

    source = json.load(Path(args.source).open(encoding="utf-8"))
    episodes = source["episode_detail"]
    if args.limit:
        episodes = episodes[: args.limit]
    source_tasks = {
        task["task_id"]: task
        for task in map(json.loads, Path(args.tasks).open(encoding="utf-8"))
    }
    missing_tasks = sorted({e["task_id"] for e in episodes} - source_tasks.keys())
    if missing_tasks:
        raise RuntimeError(f"missing tasks: {missing_tasks[:3]}")

    expected_calls = len(episodes) * (
        (1 if "SELF_REFINE_STATE_V2" in args.baselines else 0)
        + (
            args.checker_rounds
            if "CHECKER_LOOP_STATE_V2" in args.baselines
            else 0
        )
    )
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    if args.client == "mock":
        client = MockVLMClient(response="mock")
    else:
        client = DashScopeVLMClient(model=args.model, env_path=args.env_file)
    prompts = PromptLibrary()
    logger = EpisodeLogger(output_path)
    completed = done_keys(output_path) if args.resume else set()
    started = time.time()

    print(
        json.dumps(
            {
                "record_type": "fair_external_baseline_v2_start",
                "source": args.source,
                "points": len(episodes),
                "baselines": list(args.baselines),
                "expected_vlm_calls_max": expected_calls,
                "prompt_version": PROMPT_VERSION,
                "client": args.client,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    for index, episode in enumerate(episodes, 1):
        source_task = source_tasks[episode["task_id"]]
        for baseline in args.baselines:
            if (episode["task_id"], int(episode["seed"]), baseline) in completed:
                continue
            print(
                f"[fair-external-v2] point={index}/{len(episodes)} baseline={baseline} "
                f"task={episode['task_id']} seed={episode['seed']} start",
                flush=True,
            )
            record = baseline_case(
                source_task=source_task,
                episode=episode,
                baseline=baseline,
                client=client,
                prompts=prompts,
                validator=validator,
                registry=registry,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                json_mode=True,
                checker_rounds=args.checker_rounds,
                max_steps=args.max_steps_per_primitive,
                observation_size=args.observation_size,
            )
            logger.append(record)
            print(
                json.dumps(
                    {
                        "task_id": record["task_id"],
                        "seed": record["seed"],
                        "baseline": record["baseline"],
                        "rounds": record["rounds"],
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

    records = logger.read_all()
    summary = {
        "record_type": "fair_external_baseline_v2_summary",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "reference_state_arm": args.reference_state_arm,
        "points": len(episodes),
        "baselines": list(args.baselines),
        "expected_vlm_calls_max": expected_calls,
        "actual_vlm_calls": sum(r["rounds"] for r in records if r.get("record_type") == "fair_external_baseline_v2_case"),
        "prompt_version": PROMPT_VERSION,
        "execution_protocol": "frozen_initial_failure_repair_then_metaworld_execution",
        "elapsed_s": time.time() - started,
        **summarize(
            [r for r in records if r.get("record_type") == "fair_external_baseline_v2_case"]
        ),
        "output": str(output_path),
    }
    logger.append(summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
