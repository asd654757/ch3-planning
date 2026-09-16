#!/usr/bin/env python3
"""True ROUTED arm on the frozen Fair-100 perturbed closed-loop benchmark.

This runner intentionally reuses the frozen initial failures and the same
structured-state protocol as ``fair_external_baselines_v2.py``.  It does not
replay the old visual ROUTED loop.  The route is R2 first, then
R1_FROM_STATE only if the R2 plan fails symbolic validation or the goal
checker, with a hard limit of two VLM calls per point.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import os
    os.environ.setdefault("MUJOCO_GL", "egl")
except ImportError:  # pragma: no cover
    pass

from ch3.capability.registry import CapabilityRegistry
from ch3.logger.episode_logger import EpisodeLogger
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.repair import PlanRepairer
from scripts.fair_external_baselines_v2 import (
    build_execution_failure_validation,
    plan_evaluation,
    state_from_record,
)
from scripts.sim_compare_baselines import (
    execute_multiskill_plan,
    is_executable_multiskill,
)


ARM = "ROUTED_STATE_V2"
SCHEMA_VERSION = "2026-09-16-fair-routed-state-v2"
PROMPT_VERSION = "plan_repairer_r2_then_r1_from_state"


def stress_plan_generation(plan: Any) -> PlanGeneration:
    return PlanGeneration(
        plan.model_copy(deep=True),
        VLMResponse(content="", model="frozen_initial_failure", latency_ms=0),
        None,
        "frozen_initial_failure",
        "frozen_initial_failure",
        "frozen_initial_failure",
    )


def response_compact(response: Optional[VLMResponse]) -> dict[str, Any]:
    if response is None:
        return {
            "model": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "finish_reason": None,
        }
    return {
        "model": response.model,
        "prompt_tokens": int(response.prompt_tokens),
        "completion_tokens": int(response.completion_tokens),
        "total_tokens": int(response.total_tokens),
        "finish_reason": response.finish_reason,
    }


def run_routed_case(
    *,
    source_task: dict[str, Any],
    episode: dict[str, Any],
    repairer: PlanRepairer,
    validator: Any,
    registry: CapabilityRegistry,
    seed: int,
    temperature: float,
    max_tokens: int,
    max_steps: int,
    observation_size: int,
) -> dict[str, Any]:
    task_id = str(episode["task_id"])
    previous = episode["attempts"][0]
    failed_plan = source_task_plan(previous["plan"])
    current_state = state_from_record(source_task, previous["symbolic_state"])
    initial_validation = build_execution_failure_validation(source_task, episode)
    stress_generation = stress_plan_generation(failed_plan)

    started = time.time()
    r2 = repairer.repair(
        repair_mode="R2",
        task=source_task,
        initial_generation=stress_generation,
        validation=initial_validation,
        initial_state=current_state,
        seed=seed,
        temperature=temperature,
    )
    r2_validation, r2_goal_ok = plan_evaluation(
        r2.plan,
        task=source_task,
        validator=validator,
        state=current_state,
    )
    route = ["R2"]
    total_prompt_tokens = int(r2.response.prompt_tokens)
    total_completion_tokens = int(r2.response.completion_tokens)
    total_tokens = int(r2.response.total_tokens)
    total_latency_ms = int(r2.response.latency_ms)

    if r2_validation is not None and r2_validation.valid and r2_goal_ok:
        final_generation = r2
        final_validation, final_goal_ok = r2_validation, r2_goal_ok
    else:
        r1 = repairer.repair(
            repair_mode="R1_FROM_STATE",
            task=source_task,
            initial_generation=stress_generation,
            validation=initial_validation,
            initial_state=current_state,
            seed=seed,
            temperature=temperature,
        )
        route.append("R1_FROM_STATE")
        total_prompt_tokens += int(r1.response.prompt_tokens)
        total_completion_tokens += int(r1.response.completion_tokens)
        total_tokens += int(r1.response.total_tokens)
        total_latency_ms += int(r1.response.latency_ms)
        final_generation = r1
        final_validation, final_goal_ok = plan_evaluation(
            r1.plan,
            task=source_task,
            validator=validator,
            state=current_state,
        )
    repair_elapsed = time.time() - started

    sim_attempted = False
    sim_success = False
    sim_tasks: list[str] = []
    primitive_results: list[dict[str, Any]] = []
    if (
        final_generation.plan is not None
        and final_validation is not None
        and final_validation.valid
        and final_goal_ok
        and is_executable_multiskill(final_generation.plan.actions)
    ):
        sim_attempted = True
        sim_started = time.time()
        sim_success, primitive_results, sim_tasks = execute_multiskill_plan(
            final_generation.plan,
            registry,
            (task_id, seed, ARM),
            ARM,
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
    r1_response = final_generation.response if route[-1] == "R1_FROM_STATE" else None
    return {
        "record_type": "fair_routed_v2_case",
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "task_family": source_task.get("task_family"),
        "seed": seed,
        "arm": ARM,
        "baseline": ARM,
        "source_arm": "VISUAL_PROMPT_V1",
        "source_final_success": bool(episode.get("final_success")),
        "prompt_version": PROMPT_VERSION,
        "state_visibility": "full_structured_state",
        "route_taken": route,
        "fallback_triggered": route[-1] == "R1_FROM_STATE",
        "repair_mode": route[-1],
        "rounds": len(route),
        "accepted": bool(final_generation.accepted),
        "reject_reason": final_generation.reject_reason,
        "parse_error": final_generation.parse_error,
        "infeasible_reason": final_generation.infeasible_reason,
        "r2_reject_reason": r2.reject_reason,
        "r2_parse_error": r2.parse_error,
        "r2_infeasible_reason": r2.infeasible_reason,
        "r2_symbolic_valid": bool(r2_validation is not None and r2_validation.valid),
        "r2_goal_satisfied": bool(r2_goal_ok),
        "symbolic_valid": bool(final_validation is not None and final_validation.valid),
        "goal_satisfied": bool(final_goal_ok),
        "error_code": final_validation.error_code.name
        if final_validation is not None and final_validation.error_code
        else None,
        "error_layer": final_validation.layer if final_validation is not None else None,
        "error_message": final_validation.message
        if final_validation is not None
        else final_generation.parse_error or "infeasible_or_parse_error",
        "plan": (
            final_generation.plan.model_dump(mode="json")
            if final_generation.plan
            else None
        ),
        "sim_attempted": sim_attempted,
        "sim_success": bool(sim_success),
        "final_success": bool(
            final_validation is not None
            and final_validation.valid
            and final_goal_ok
            and sim_success
        ),
        "sim_tasks": sim_tasks,
        "primitive_results": compact_primitives,
        "repair_elapsed_s": repair_elapsed,
        "sim_elapsed_s": sim_elapsed,
        "prompt_id": final_generation.prompt_id,
        "prompt_hash": final_generation.prompt_hash,
        "response": response_compact(final_generation.response),
        "r2_response": response_compact(r2.response),
        "r1_response": response_compact(r1_response),
        "cost": {
            "rounds": len(route),
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": total_latency_ms,
        },
    }


def source_task_plan(raw_plan: dict[str, Any]) -> Any:
    from ch3.schema.model_plan import ModelPlan

    return ModelPlan.model_validate(raw_plan)


def summarize_routed(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in records if r.get("record_type") == "fair_routed_v2_case"]
    points = len(rows)
    by_family: dict[str, dict[str, Any]] = defaultdict(dict)
    for family in sorted({r["task_family"] for r in rows}):
        family_rows = [r for r in rows if r["task_family"] == family]
        by_family[family] = {
            "points": len(family_rows),
            "r2_direct_success": sum(
                r["r2_symbolic_valid"] and r["r2_goal_satisfied"] for r in family_rows
            ),
            "fallback": sum(r["fallback_triggered"] for r in family_rows),
            "symbolic_valid": sum(r["symbolic_valid"] for r in family_rows),
            "goal_satisfied": sum(r["goal_satisfied"] for r in family_rows),
            "sim_success": sum(r["sim_success"] for r in family_rows),
            "final_success": sum(r["final_success"] for r in family_rows),
            "final_success_rate": (
                sum(r["final_success"] for r in family_rows) / len(family_rows)
                if family_rows
                else None
            ),
        }
    return {
        "ROUTED": {
            "points": points,
            "rounds": sum(r["rounds"] for r in rows),
            "r2_direct_success": sum(
                r["r2_symbolic_valid"] and r["r2_goal_satisfied"] for r in rows
            ),
            "fallback": sum(r["fallback_triggered"] for r in rows),
            "symbolic_valid": sum(r["symbolic_valid"] for r in rows),
            "goal_satisfied": sum(r["goal_satisfied"] for r in rows),
            "sim_attempted": sum(r["sim_attempted"] for r in rows),
            "sim_success": sum(r["sim_success"] for r in rows),
            "final_success": sum(r["final_success"] for r in rows),
            "parse_error": sum(r["parse_error"] is not None for r in rows),
            "prompt_tokens": sum(r["cost"]["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["cost"]["completion_tokens"] for r in rows),
            "total_tokens": sum(r["cost"]["total_tokens"] for r in rows),
            "final_success_rate": (
                sum(r["final_success"] for r in rows) / points if points else None
            ),
            "avg_rounds": sum(r["rounds"] for r in rows) / points if points else None,
        },
        "by_family": by_family,
    }


def mcnemar_exact_p(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1))
    return min(1.0, 2.0 * tail * (0.5**n))


def done_keys(path: Path) -> set[tuple[str, int, str]]:
    if not path.exists():
        return set()
    keys: set[tuple[str, int, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("record_type") == "fair_routed_v2_case":
            keys.add((str(row["task_id"]), int(row["seed"]), ARM))
    return keys


def load_outcomes(
    *,
    fair_path: Optional[Path],
    r1_path: Optional[Path],
    fair_arms: list[str],
    points: set[tuple[str, int]],
) -> dict[str, dict[tuple[str, int], bool]]:
    outcomes: dict[str, dict[tuple[str, int], bool]] = {}
    if fair_path is not None:
        for line in fair_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("record_type") != "fair_external_baseline_v2_case":
                continue
            if row.get("baseline") not in fair_arms:
                continue
            key = (str(row["task_id"]), int(row["seed"]))
            if key in points:
                outcomes.setdefault(str(row["baseline"]), {})[key] = bool(
                    row["final_success"]
                )
    if r1_path is not None:
        for line in r1_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("record_type") != "visual_prompt_v2_repair_rerun_case":
                continue
            if row.get("arm") != "STATE_PROMPT_V2":
                continue
            key = (str(row["task_id"]), int(row["seed"]))
            if key in points:
                outcomes.setdefault("R1_FROM_STATE", {})[key] = bool(
                    row["final_success"]
                )
    return outcomes


def compare_with_references(
    *,
    routed_records: list[dict[str, Any]],
    outcomes: dict[str, dict[tuple[str, int], bool]],
) -> dict[str, Any]:
    routed = {
        (str(r["task_id"]), int(r["seed"])): bool(r["final_success"])
        for r in routed_records
        if r.get("record_type") == "fair_routed_v2_case"
    }
    comparisons: dict[str, Any] = {}
    for method, method_outcomes in sorted(outcomes.items()):
        common = sorted(set(routed) & set(method_outcomes))
        counts = Counter(
            (routed[key], method_outcomes[key]) for key in common
        )
        both = counts[(True, True)]
        only_routed = counts[(True, False)]
        only_other = counts[(False, True)]
        neither = counts[(False, False)]
        comparisons[method] = {
            "points": len(common),
            "ROUTED_success": sum(routed[key] for key in common),
            "OTHER_success": sum(method_outcomes[key] for key in common),
            "paired_counts": {
                "both_success": both,
                "only_ROUTED_success": only_routed,
                f"only_{method}_success": only_other,
                "both_fail": neither,
            },
            "discordant": only_routed + only_other,
            "mcnemar_exact_p_value": mcnemar_exact_p(only_routed, only_other),
        }
    return comparisons


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument(
        "--env-file",
        default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env",
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
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 means all 100 points")
    parser.add_argument(
        "--fair-baselines",
        default="data/collections/fair_external_baselines_v2_20260915_025622.jsonl",
    )
    parser.add_argument(
        "--r1-reference",
        default="data/collections/visual_prompt_v2_repair_rerun_20260914_210737.jsonl",
    )
    parser.add_argument(
        "--fair-arms",
        nargs="+",
        default=["SELF_REFINE_STATE_V2", "CHECKER_LOOP_STATE_V2"],
    )
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--client", choices=["real", "mock"], default="real")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    output_path = Path(args.output)
    if output_path.exists() and not args.resume:
        raise FileExistsError(
            f"Refusing to overwrite existing output: {output_path}. "
            "Use a new timestamped output or --resume."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

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

    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = __import__("ch3.validator.pipeline", fromlist=["Validator"]).Validator(
        scene_objects=set(),
        registry=registry,
    )
    prompts = PromptLibrary()
    if args.client == "mock":
        client = MockVLMClient(response="mock")
    else:
        client = DashScopeVLMClient(model=args.model, env_path=args.env_file)
    repairer = PlanRepairer(
        client=client,
        prompts=prompts,
        max_tokens=args.max_tokens,
        json_mode=True,
    )

    started = time.time()
    logger = EpisodeLogger(output_path)
    completed = done_keys(output_path) if args.resume else set()
    print(
        json.dumps(
            {
                "record_type": "fair_routed_v2_start",
                "source": args.source,
                "points": len(episodes),
                "arm": ARM,
                "expected_vlm_calls_max": len(episodes) * 2,
                "prompt_version": PROMPT_VERSION,
                "client": args.client,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    if not args.summary_only:
        for index, episode in enumerate(episodes, 1):
            key = (str(episode["task_id"]), int(episode["seed"]), ARM)
            if key in completed:
                continue
            print(
                f"[fair-routed-v2] point={index}/{len(episodes)} "
                f"task={episode['task_id']} seed={episode['seed']} start",
                flush=True,
            )
            record = run_routed_case(
                source_task=source_tasks[episode["task_id"]],
                episode=episode,
                repairer=repairer,
                validator=validator,
                registry=registry,
                seed=int(episode["seed"]),
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                max_steps=args.max_steps_per_primitive,
                observation_size=args.observation_size,
            )
            logger.append(record)
            print(
                json.dumps(
                    {
                        "task_id": record["task_id"],
                        "seed": record["seed"],
                        "route_taken": record["route_taken"],
                        "rounds": record["rounds"],
                        "r2_symbolic_valid": record["r2_symbolic_valid"],
                        "r2_goal_satisfied": record["r2_goal_satisfied"],
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
    routed_records = [
        r for r in records if r.get("record_type") == "fair_routed_v2_case"
    ]
    point_keys = {
        (str(e["task_id"]), int(e["seed"])) for e in episodes
    }
    outcomes = load_outcomes(
        fair_path=Path(args.fair_baselines) if args.fair_baselines else None,
        r1_path=Path(args.r1_reference) if args.r1_reference else None,
        fair_arms=args.fair_arms,
        points=point_keys,
    )
    comparisons = compare_with_references(
        routed_records=routed_records,
        outcomes=outcomes,
    )
    summary = {
        "record_type": "fair_routed_v2_summary",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "fair_baselines": args.fair_baselines,
        "r1_reference": args.r1_reference,
        "points": len(episodes),
        "completed_points": len(routed_records),
        "arm": ARM,
        "expected_vlm_calls_max": len(episodes) * 2,
        "actual_vlm_calls": sum(r["rounds"] for r in routed_records),
        "prompt_version": PROMPT_VERSION,
        "execution_protocol": (
            "frozen_initial_failure_repair_then_metaworld_execution"
        ),
        "elapsed_s": time.time() - started,
        **summarize_routed(routed_records),
        "paired_comparisons": comparisons,
        "output": str(output_path),
    }
    logger.append(summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
