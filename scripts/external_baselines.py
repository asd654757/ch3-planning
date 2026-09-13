#!/usr/bin/env python3
"""External-baseline runner for the frozen repair-pressure cases.

The runner reuses the exact stress plans recorded by ``repair_pressure.py`` so
Self-Refine and AutoTAMP-style checker-loop see the same task/seed/pressure
distribution as R0/R1/R2.  It does not re-run the baseline planner and does not
modify any frozen formal collection.

Baselines:
- ``self_refine``: one model self-review call, no validator feedback.
- ``checker_loop``: up to two model calls with deterministic checker feedback,
  full-plan regeneration and no prefix lock.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.logger.episode_logger import EpisodeLogger
from ch3.schema.model_plan import ModelPlan
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.collector import (
    evaluate_plan,
    infeasible_result,
    load_scenarios,
    make_record,
    schema_error_result,
    world_state_from_task,
)
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.parser import PlanParseError, parse_structured_plan_or_infeasible
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.prompts import PromptLibrary
from ch3.validator.pipeline import Validator

BASELINES = ("self_refine", "checker_loop")
SCHEMA_VERSION = "2026-09-11-external-baseline-v1"


def load_source_cases(
    path: str | Path,
    *,
    pressure_types: Optional[Iterable[str]] = None,
    task_ids: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    wanted_pressure = set(pressure_types) if pressure_types else None
    wanted_tasks = set(task_ids) if task_ids else None
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid source JSON on line {line_number}: {exc}") from exc
            if record.get("record_type") != "repair_pressure":
                continue
            if wanted_pressure is not None and record.get("pressure_type") not in wanted_pressure:
                continue
            if wanted_tasks is not None and record.get("task_id") not in wanted_tasks:
                continue
            case_key = (
                str(record.get("task_id")),
                int(record.get("seed", -1)),
                str(record.get("pressure_type")),
            )
            # A frozen collection has one row per R0/R1/R2, but all three share
            # the same deterministic stress case. External baselines need one.
            if case_key in seen:
                continue
            seen.add(case_key)
            required = {"task_id", "seed", "pressure_type", "stress_plan"}
            missing = required - record.keys()
            if missing:
                raise ValueError(f"Source record line {line_number} missing fields: {sorted(missing)}")
            cases.append(record)
    if not cases:
        raise ValueError("No matching repair-pressure cases in source collection")
    return cases


def output_keys(path: str | Path) -> set[tuple[str, int, str, str]]:
    if not Path(path).exists():
        return set()
    keys = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            keys.add(
                (
                    str(row.get("task_id")),
                    int(row.get("seed", -1)),
                    str(row.get("pressure_type")),
                    str(row.get("repair_mode")),
                )
            )
    return keys


def _prompt_values(
    task: Mapping[str, Any],
    state: Any,
    plan: ModelPlan,
) -> dict[str, Any]:
    return {
        "instruction": task["instruction"],
        "objects": sorted(task["objects"]),
        "goal": task["goal"],
        "current_state": sorted(state.facts() | state.empty_hand_facts({"left", "right"})),
        "current_plan": plan.model_dump(),
        "failed_plan": plan.model_dump(),
    }


def _parse_generation(
    response: VLMResponse,
    prompt: str,
    prompt_id: str,
    prompt_hash: str,
    *,
    repair_mode: str,
    rounds: int = 1,
    total_tokens: int = 0,
    total_latency_ms: int = 0,
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
        generation = PlanGeneration(
            None,
            response,
            str(exc),
            prompt,
            prompt_id,
            prompt_hash,
        )
        generation.__dict__["baseline_rounds"] = rounds
        generation.__dict__["baseline_total_tokens"] = total_tokens
        generation.__dict__["baseline_total_latency_ms"] = total_latency_ms
        return generation


def _aggregate_metadata(generation: PlanGeneration, rounds: int, total_tokens: int, total_latency_ms: int) -> None:
    generation.__dict__["baseline_rounds"] = rounds
    generation.__dict__["baseline_total_tokens"] = total_tokens
    generation.__dict__["baseline_total_latency_ms"] = total_latency_ms


def run_self_refine(
    *,
    task: Mapping[str, Any],
    stress_plan: ModelPlan,
    state: Any,
    client: Any,
    prompts: PromptLibrary,
    seed: int,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> PlanGeneration:
    values = _prompt_values(task, state, stress_plan)
    prompt, prompt_id, prompt_hash = prompts.render("external_self_refine", **values)
    response = client.complete(
        system_prompt=(
            "You are a robotics planner performing self-refinement. Output only "
            "one JSON object. Do not add commentary or Markdown."
        ),
        user_prompt=prompt,
        image_path=task.get("image_path"),
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
        json_mode=json_mode,
    )
    generation = _parse_generation(response, prompt, prompt_id, prompt_hash, repair_mode="self_refine")
    _aggregate_metadata(generation, 1, response.total_tokens, response.latency_ms)
    return generation


def run_checker_loop(
    *,
    task: Mapping[str, Any],
    stress_plan: ModelPlan,
    stress_validation: Any,
    state: Any,
    client: Any,
    prompts: PromptLibrary,
    validator: Validator,
    seed: int,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
    max_rounds: int = 2,
) -> PlanGeneration:
    current_plan = stress_plan
    validation = stress_validation
    parse_error: Optional[str] = None
    returned_plan: Optional[ModelPlan] = None
    total_tokens = 0
    total_latency_ms = 0
    generation: Optional[PlanGeneration] = None

    for round_number in range(1, max_rounds + 1):
        feedback = {
            "round": round_number,
            "first_invalid_step": getattr(validation, "first_invalid_step", None)
            if validation is not None
            else None,
            "error_code": validation.error_code.value
            if validation is not None and validation.error_code
            else None,
            "error_layer": getattr(validation, "layer", None) if validation is not None else None,
            "error_message": getattr(validation, "message", "") if validation is not None else "",
            "validated_prefix_step_ids": [a.step_id for a in getattr(validation, "validated_prefix", [])]
            if validation is not None
            else [],
        }
        if parse_error is not None:
            feedback.update(
                {
                    "first_invalid_step": None,
                    "error_code": "SCHEMA_ERROR",
                    "error_layer": "syntax",
                    "error_message": parse_error,
                    "validated_prefix_step_ids": [],
                }
            )
        values = _prompt_values(task, state, current_plan)
        values["checker_feedback"] = feedback
        prompt, prompt_id, prompt_hash = prompts.render("external_checker_loop", **values)
        response = client.complete(
            system_prompt=(
                "You are a robotics plan repairer in a checker loop. Output only "
                "one JSON object. Do not add commentary or Markdown."
            ),
            user_prompt=prompt,
            image_path=task.get("image_path"),
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            json_mode=json_mode,
        )
        total_tokens += response.total_tokens
        total_latency_ms += response.latency_ms
        try:
            returned_plan, infeasible_reason = parse_structured_plan_or_infeasible(response.content)
            parse_error = None
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
            parse_error = str(exc)
            generation = PlanGeneration(
                None,
                response,
                parse_error,
                prompt,
                prompt_id,
                prompt_hash,
            )

        # A model refusal terminates the loop without further checker feedback.
        if generation.infeasible:
            break

        if returned_plan is not None:
            current_plan = returned_plan
            validation, _, _ = evaluate_plan(returned_plan, task, validator, state)
            if validation.valid:
                break
        else:
            validation = None

    assert generation is not None
    _aggregate_metadata(generation, round_number, total_tokens, total_latency_ms)
    return generation


def baseline_record(
    *,
    task: Mapping[str, Any],
    seed: int,
    pressure_type: str,
    source_record: Mapping[str, Any],
    stress_plan: ModelPlan,
    stress_validation: Any,
    generation: PlanGeneration,
    plan: Optional[ModelPlan],
    validation: Any,
    goal_ok: bool,
    pbw: bool,
) -> dict[str, Any]:
    repair_mode = str(generation.__dict__.get("baseline_mode", "unknown"))
    record = make_record(
        task=task,
        seed=seed,
        record_type="repair_pressure",
        baseline="external",
        generation=generation,
        plan=plan,
        validation=validation,
        goal_ok=goal_ok,
        pass_but_wrong=pbw,
    )
    record.update(
        {
            "schema_version": SCHEMA_VERSION,
            "record_type": "repair_pressure",
            "repair_mode": repair_mode,
            "benchmark": source_record.get("benchmark", "unspecified"),
            "pressure_type": pressure_type,
            "pressure_source_plan": source_record.get("pressure_source_plan"),
            "pressure_source_valid": source_record.get("pressure_source_valid", True),
            "stress_plan": source_record.get("stress_plan"),
            "stress_valid": bool(source_record.get("stress_valid", False)),
            "stress_error_code": source_record.get("stress_error_code"),
            "stress_error_layer": source_record.get("stress_error_layer"),
            "stress_error_message": source_record.get("stress_error_message"),
            "stress_first_invalid_step": source_record.get("stress_first_invalid_step"),
            "stress_validated_prefix": source_record.get("stress_validated_prefix", []),
            "baseline_rounds": generation.__dict__.get("baseline_rounds", 1),
            "baseline_total_tokens": generation.__dict__.get("baseline_total_tokens", 0),
            "baseline_total_latency_ms": generation.__dict__.get(
                "baseline_total_latency_ms", 0
            ),
        }
    )
    return record


def run_baseline_case(
    case: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    baseline: str,
    client: Any,
    validator: Validator,
    logger: EpisodeLogger,
    prompts: PromptLibrary,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    json_mode: bool = True,
) -> dict[str, Any]:
    seed = int(case["seed"])
    pressure_type = str(case["pressure_type"])
    stress_plan = ModelPlan.model_validate(case["stress_plan"])
    state = world_state_from_task(task)
    validator.scene_objects = set(task["objects"])
    stress_validation = validator.validate(stress_plan, state)
    if stress_validation.valid:
        raise ValueError(
            f"Source stress plan unexpectedly valid: {task['task_id']} / {pressure_type}"
        )

    if baseline == "self_refine":
        generation = run_self_refine(
            task=task,
            stress_plan=stress_plan,
            state=state,
            client=client,
            prompts=prompts,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
    elif baseline == "checker_loop":
        generation = run_checker_loop(
            task=task,
            stress_plan=stress_plan,
            stress_validation=stress_validation,
            state=state,
            client=client,
            prompts=prompts,
            validator=validator,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
    else:
        raise ValueError(f"Unknown baseline: {baseline}")

    generation.__dict__["baseline_mode"] = baseline
    if generation.infeasible:
        validation = infeasible_result(generation.infeasible_reason or "task infeasible")
        goal_ok, pbw = False, False
    elif generation.plan is None:
        validation = schema_error_result(generation.parse_error or "plan_parse_error")
        goal_ok, pbw = False, False
    else:
        validation, goal_ok, pbw = evaluate_plan(
            generation.plan, task, validator, state
        )
    record = baseline_record(
        task=task,
        seed=seed,
        pressure_type=pressure_type,
        source_record=case,
        stress_plan=stress_plan,
        stress_validation=stress_validation,
        generation=generation,
        plan=generation.plan,
        validation=validation,
        goal_ok=goal_ok,
        pbw=pbw,
    )
    logger.append(record)
    return {
        "task_id": task["task_id"],
        "seed": seed,
        "pressure_type": pressure_type,
        "baseline": baseline,
        "rounds": generation.__dict__.get("baseline_rounds", 1),
        "valid": bool(validation and validation.valid),
        "goal_satisfied": goal_ok,
        "pass_but_wrong": pbw,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-collection",
        required=True,
        help="Frozen repair-pressure JSONL used as the exact case source",
    )
    parser.add_argument(
        "--scenarios",
        default="data/scenarios/stress_tasks_v8_pilot_v2.jsonl",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="New timestamped external-baseline JSONL output",
    )
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--env-file",
        default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--client", choices=["real", "mock"], default="real")
    parser.add_argument("--no-json-mode", action="store_true")
    parser.add_argument(
        "--baselines",
        nargs="+",
        choices=BASELINES,
        default=list(BASELINES),
    )
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--checker-rounds", type=int, default=2)
    parser.add_argument("--pressure-types", nargs="+")
    parser.add_argument("--task-ids", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1")
    if args.checker_rounds < 1:
        raise ValueError("--checker-rounds must be >= 1")
    output_path = Path(args.output)
    if output_path.exists() and not args.append and not args.resume:
        raise FileExistsError(
            f"Refusing to overwrite existing external-baseline collection: {output_path}. "
            "Use a new timestamped output, --append, or --resume."
        )

    cases = load_source_cases(
        args.source_collection,
        pressure_types=args.pressure_types,
        task_ids=args.task_ids,
    )
    if args.limit is not None:
        cases = cases[: args.limit]
    scenarios = load_scenarios(args.scenarios)
    scenario_by_id = {task["task_id"]: task for task in scenarios}
    missing_tasks = sorted({c["task_id"] for c in cases} - scenario_by_id.keys())
    if missing_tasks:
        raise ValueError(f"Task IDs missing from scenarios: {missing_tasks}")

    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    if args.client == "mock":
        client = MockVLMClient(response="mock")
    else:
        client_kwargs: dict[str, Any] = {"model": args.model}
        if args.base_url:
            client_kwargs["base_url"] = args.base_url
        if args.env_file:
            client_kwargs["env_path"] = args.env_file
        if args.api_key:
            client_kwargs["api_key"] = args.api_key
        client = DashScopeVLMClient(**client_kwargs)

    prompts = PromptLibrary()
    logger = EpisodeLogger(output_path)
    done = output_keys(output_path)
    summaries = []
    started = time.time()
    for case in cases:
        task = scenario_by_id[case["task_id"]]
        for baseline in args.baselines:
            key = (
                str(case["task_id"]),
                int(case["seed"]),
                str(case["pressure_type"]),
                baseline,
            )
            if (args.append or args.resume) and key in done:
                continue
            print(
                f"[external] {task['task_id']} seed={case['seed']} "
                f"pressure={case['pressure_type']} baseline={baseline}",
                flush=True,
            )
            summary = run_baseline_case(
                case,
                task,
                baseline=baseline,
                client=client,
                validator=validator,
                logger=logger,
                prompts=prompts,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                json_mode=not args.no_json_mode,
            )
            if baseline == "checker_loop":
                summary["checker_rounds"] = args.checker_rounds
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(
        json.dumps(
            {
                "completed_cases": len(summaries),
                "baselines": list(args.baselines),
                "elapsed_s": round(time.time() - started, 3),
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
