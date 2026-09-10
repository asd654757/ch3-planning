"""Batch collector for the frozen four-arm VLM experiment.

For each (task, seed) this emits:
1. one shared initial ModelPlan record for B1/B2a/B2b;
2. one repair record per requested repair group when that shared plan is invalid;
3. one independent B0 direct-plan record.

Every JSONL line contains the raw model output, rendered prompt, prompt hash,
validation result, goal check, tokens, and latency.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.errors import ErrorCode
from ch3.logger.episode_logger import EpisodeLogger
from ch3.protocols.frozen import REPAIR_GROUPS
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.planner import DirectPlanner, InitialPlanner, PlanGeneration
from ch3.vlm.repair import PlanRepairer, RepairGeneration


SHARED_BASELINE = "B1_B2A_B2B_SHARED"


def load_scenarios(path: str | Path) -> list[dict[str, Any]]:
    scenarios = []
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                task = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid scenario JSON on line {line_number}: {exc}") from exc
            required = {"task_id", "difficulty", "instruction", "objects", "initial_state", "goal"}
            missing = required - set(task)
            if missing:
                raise ValueError(f"Scenario line {line_number} missing fields: {sorted(missing)}")
            scenarios.append(task)
    if not scenarios:
        raise ValueError(f"No scenarios loaded from {path}")
    return scenarios


def world_state_from_task(task: Mapping[str, Any]) -> WorldState:
    """Build the frozen discrete state from a scenario record."""
    state_data = task["initial_state"]
    objects = set(task["objects"])
    at = {str(obj): str(surface) for obj, surface in state_data.get("at", {}).items()}
    holding = {
        str(arm): str(obj) for arm, obj in state_data.get("holding", {}).items()
    }
    missing = objects - set(at)
    if missing:
        raise ValueError(f"Objects have no initial location: {sorted(missing)}")
    return WorldState(objects=objects, at=at, holding=holding)


def evaluate_plan(
    plan: Optional[ModelPlan],
    task: Mapping[str, Any],
    validator: Validator,
    initial_state: WorldState,
) -> tuple[Optional[ValidationResult], bool, bool]:
    """Return (validation, goal_satisfied, pass_but_wrong)."""
    if plan is None:
        return None, False, False
    result = validator.validate(plan, initial_state)
    goal = GoalSpec.model_validate(task["goal"])
    satisfied = result.valid and goal_satisfied(
        result.final_state or initial_state, goal, validator.registry.arms
    )
    pass_but_wrong = bool(result.valid and not satisfied)
    return result, satisfied, pass_but_wrong


def validation_summary(
    validation: Optional[ValidationResult],
    *,
    parse_error: Optional[str] = None,
    goal_satisfied: bool = False,
    pass_but_wrong: bool = False,
) -> dict[str, Any]:
    if validation is None:
        return {
            "format_valid": False,
            "object_valid": False,
            "capability_valid": False,
            "state_valid": False,
            "valid": False,
            "first_invalid_step": None,
            "error_code": "E01",
            "error_layer": "syntax",
            "error_message": parse_error or "plan_parse_error",
            "validated_prefix_step_ids": [],
            "final_state_facts": [],
            "goal_satisfied": goal_satisfied,
            "pass_but_wrong": pass_but_wrong,
        }
    error_code = validation.error_code.value if validation.error_code else None
    layer = validation.layer
    protocol_refusal = layer == "protocol"
    return {
        "format_valid": not protocol_refusal and (layer not in {"syntax"} if not validation.valid else True),
        "object_valid": not protocol_refusal and not (
            validation.valid is False and layer == "object"
        ),
        "capability_valid": not protocol_refusal and not (
            validation.valid is False and layer == "capability"
        ),
        "state_valid": not protocol_refusal and not (
            validation.valid is False and layer == "state"
        ),
        "valid": validation.valid,
        "first_invalid_step": validation.first_invalid_step,
        "error_code": error_code,
        "error_layer": layer,
        "error_message": validation.message,
        "validated_prefix_step_ids": [a.step_id for a in validation.validated_prefix],
        "final_state_facts": sorted(
            validation.final_state.facts() | validation.final_state.empty_hand_facts({"left", "right"})
        )
        if validation.final_state
        else [],
        "goal_satisfied": goal_satisfied,
        "pass_but_wrong": pass_but_wrong,
    }


def response_summary(response: VLMResponse) -> dict[str, Any]:
    return {
        "raw_vlm_output": response.content,
        "model": response.model,
        "finish_reason": response.finish_reason,
        "response_id": response.response_id,
        "request_id": response.request_id,
        "input_tokens": response.prompt_tokens,
        "output_tokens": response.completion_tokens,
        "total_tokens": response.total_tokens,
        "latency_ms": response.latency_ms,
        "raw_api_response": response.raw,
    }


def plan_summary(plan: Optional[ModelPlan], parse_error: Optional[str] = None) -> dict[str, Any]:
    return {
        "model_plan": plan.model_dump() if plan else None,
        "parse_error": parse_error,
    }


def base_record(
    *,
    task: Mapping[str, Any],
    seed: int,
    record_type: str,
    baseline: str,
    generation: PlanGeneration | RepairGeneration,
) -> dict[str, Any]:
    return {
        "schema_version": "2026-09-08",
        "record_type": record_type,
        "ts": time.time(),
        "task_id": task["task_id"],
        "difficulty": task["difficulty"],
        "seed": seed,
        "baseline": baseline,
        "repair_mode": getattr(generation, "repair_mode", None),
        "natural_injected": bool(task.get("natural_injected", False)),
        "prompt_id": generation.prompt_id,
        "prompt_version": generation.prompt_hash,
        "prompt_hash": generation.prompt_hash,
        "prompt": generation.prompt,
        "accepted": bool(getattr(generation, "accepted", True)),
        "reject_reason": getattr(generation, "reject_reason", None),
        "locked_prefix_step_ids": list(
            getattr(generation, "locked_prefix_step_ids", ())
        ),
        "merged_with_prefix": bool(getattr(generation, "merged_with_prefix", False)),
    }


def make_record(
    *,
    task: Mapping[str, Any],
    seed: int,
    record_type: str,
    baseline: str,
    generation: PlanGeneration | RepairGeneration,
    plan: Optional[ModelPlan],
    validation: Optional[ValidationResult],
    goal_ok: bool,
    pass_but_wrong: bool,
) -> dict[str, Any]:
    record = base_record(
        task=task,
        seed=seed,
        record_type=record_type,
        baseline=baseline,
        generation=generation,
    )
    record.update(response_summary(generation.response))
    record.update(plan_summary(plan, generation.parse_error))
    record.update(
        validation_summary(
            validation,
            parse_error=generation.parse_error,
            goal_satisfied=goal_ok,
            pass_but_wrong=pass_but_wrong,
        )
    )
    if getattr(generation, "infeasible", False):
        record["response_protocol"] = "infeasible"
        record["infeasible_reason"] = generation.infeasible_reason
    return record


def schema_error_result(message: str) -> ValidationResult:
    from ch3.errors import ErrorCode

    return ValidationResult(
        valid=False,
        first_invalid_step=None,
        error_code=ErrorCode.SCHEMA_ERROR,
        message=message,
        layer="syntax",
        validated_prefix=[],
    )


def infeasible_result(reason: str) -> ValidationResult:
    """Create a validation record for an accepted explicit refusal."""
    return ValidationResult(
        valid=False,
        first_invalid_step=None,
        error_code=ErrorCode.INFEASIBLE_RESPONSE,
        message=reason,
        layer="protocol",
        validated_prefix=[],
    )


def collect_one(
    task: Mapping[str, Any],
    seed: int,
    *,
    planner: InitialPlanner,
    direct_planner: DirectPlanner,
    repairer: PlanRepairer,
    validator: Validator,
    logger: EpisodeLogger,
    repair_groups: Iterable[str] = ("R0", "R1", "R2"),
) -> dict[str, Any]:
    """Run and log one task/seed slice. Returns a compact run summary."""
    initial_state = world_state_from_task(task)
    calls = 0
    shared = planner.plan(task, seed=seed)
    calls += 1

    if getattr(shared, "infeasible", False):
        validation = infeasible_result(shared.infeasible_reason or "task infeasible")
        goal_ok, pass_but_wrong = False, False
    elif shared.plan is None:
        validation, goal_ok, pass_but_wrong = schema_error_result(
            shared.parse_error or "plan_parse_error"
        ), False, False
    else:
        validation, goal_ok, pass_but_wrong = evaluate_plan(
            shared.plan, task, validator, initial_state
        )

    shared_record = make_record(
        task=task,
        seed=seed,
        record_type="initial_shared_plan",
        baseline=SHARED_BASELINE,
        generation=shared,
        plan=shared.plan,
        validation=validation,
        goal_ok=goal_ok,
        pass_but_wrong=pass_but_wrong,
    )
    logger.append(shared_record)

    repair_summaries: dict[str, dict[str, Any]] = {}
    if validation is not None and not validation.valid:
        for repair_mode in repair_groups:
            if repair_mode not in REPAIR_GROUPS:
                raise ValueError(f"Unknown repair group: {repair_mode}")
            repaired = repairer.repair(
                repair_mode=repair_mode,
                task=task,
                initial_generation=shared,
                validation=validation,
                initial_state=initial_state,
                seed=seed,
            )
            calls += 1
            if getattr(repaired, "infeasible", False):
                repair_validation = infeasible_result(
                    repaired.infeasible_reason or "task infeasible"
                )
            elif repaired.plan is None:
                repair_validation = None
            else:
                repair_validation, repair_goal_ok, repair_pbw = evaluate_plan(
                    repaired.plan, task, validator, initial_state
                )
            if repaired.plan is None:
                repair_goal_ok, repair_pbw = False, False
            repair_record = make_record(
                task=task,
                seed=seed,
                record_type="repair",
                baseline="B2B",
                generation=repaired,
                plan=repaired.plan,
                validation=repair_validation,
                goal_ok=repair_goal_ok,
                pass_but_wrong=repair_pbw,
            )
            logger.append(repair_record)
            repair_summaries[repair_mode] = {
                "accepted": repaired.accepted,
                "reject_reason": repaired.reject_reason,
                "valid": bool(repair_validation and repair_validation.valid),
                "goal_satisfied": repair_goal_ok,
                "pass_but_wrong": repair_pbw,
                "llm_calls": 1,
            }

    b0 = direct_planner.plan(task, seed=seed)
    calls += 1
    if getattr(b0, "infeasible", False):
        b0_validation: Optional[ValidationResult] = infeasible_result(
            b0.infeasible_reason or "task infeasible"
        )
        b0_goal_ok, b0_pbw = False, False
    elif b0.plan is None:
        b0_validation: Optional[ValidationResult] = schema_error_result(
            b0.parse_error or "plan_parse_error"
        )
        b0_goal_ok, b0_pbw = False, False
    else:
        # Evaluation is metric-only; B0 still does not consume validation at runtime.
        b0_validation, b0_goal_ok, b0_pbw = evaluate_plan(
            b0.plan, task, validator, initial_state
        )
    b0_record = make_record(
        task=task,
        seed=seed,
        record_type="direct_b0_plan",
        baseline="B0",
        generation=b0,
        plan=b0.plan,
        validation=b0_validation,
        goal_ok=b0_goal_ok,
        pass_but_wrong=b0_pbw,
    )
    logger.append(b0_record)

    return {
        "task_id": task["task_id"],
        "seed": seed,
        "llm_calls_in_slice": calls,
        "shared_valid": bool(validation and validation.valid),
        "shared_goal_satisfied": goal_ok,
        "repairs": repair_summaries,
        "b0_valid": bool(b0_validation and b0_validation.valid),
        "b0_goal_satisfied": b0_goal_ok,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        default="config/scenarios_pilot.jsonl",
        help="JSONL scenario file",
    )
    parser.add_argument(
        "--output",
        default="data/collections/vlm_pilot.jsonl",
        help="JSONL collection output on the data disk",
    )
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--model", default="qwen-vl-plus")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the OpenAI-compatible base URL (e.g. local vLLM/SGLang)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to a .env file containing DASHSCOPE_API_KEY (overrides default lookup)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key override (prefer --env-file or DASHSCOPE_API_KEY env var for security)",
    )
    parser.add_argument(
        "--client",
        choices=["real", "mock"],
        default="real",
        help="Use 'mock' for deterministic offline testing without any API call",
    )
    parser.add_argument(
        "--no-json-mode",
        action="store_true",
        help="Disable response_format json_object (for local models that don't support it)",
    )
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument(
        "--repair-groups",
        nargs="+",
        choices=sorted(REPAIR_GROUPS),
        default=sorted(REPAIR_GROUPS),
    )
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--repair-temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--task-ids", nargs="*", help="optional task filter")
    parser.add_argument("--difficulties", nargs="*", help="optional difficulty filter")
    parser.add_argument("--limit", type=int, help="max scenarios after filtering")
    parser.add_argument(
        "--no-image",
        action="store_true",
        help="ignore scenario image_path and send text-only prompts",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="append to an existing output file instead of refusing to overwrite it",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume from an existing output file: skip already-completed (task_id, seed) pairs",
    )
    return parser


def load_completed_keys(output_path: Path) -> set[tuple[str, int]]:
    """Read an existing JSONL file and return the set of completed (task_id, seed) pairs."""
    if not output_path.is_file():
        return set()
    keys: set[tuple[str, int]] = set()
    with open(output_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "task_id" in rec and "seed" in rec:
                keys.add((rec["task_id"], int(rec["seed"])))
    return keys


def _default_mock_response(payload: Mapping[str, Any]) -> str:
    """Return a deterministic valid plan for offline mock testing."""
    return json.dumps(
        {
            "goal": {"type": "reach", "object": "any"},
            "actions": [
                {"step_id": 1, "type": "pick", "object": "red_block", "arm": "left"},
                {
                    "step_id": 2,
                    "type": "place",
                    "object": "red_block",
                    "arm": "left",
                    "target": "table",
                },
            ],
        },
        ensure_ascii=False,
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.seeds < 1:
        raise ValueError("--seeds must be >= 1")
    output_path = Path(args.output)

    completed_keys: set[tuple[str, int]] = set()
    if output_path.exists() and not args.append and not args.resume:
        raise FileExistsError(
            f"Refusing to overwrite existing collection: {output_path}. "
            "Use --append, --resume, or choose a new timestamped output file."
        )
    if args.resume:
        completed_keys = load_completed_keys(output_path)
        print(f"Resume: found {len(completed_keys)} completed (task_id, seed) pairs")


    scenarios = load_scenarios(args.scenarios)
    if args.task_ids:
        wanted = set(args.task_ids)
        scenarios = [task for task in scenarios if task["task_id"] in wanted]
    if args.difficulties:
        wanted = set(args.difficulties)
        scenarios = [task for task in scenarios if task["difficulty"] in wanted]
    if args.limit is not None:
        scenarios = scenarios[: args.limit]

    if args.no_image:
        scenarios = [{**task, "image_path": None} for task in scenarios]

    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(
        scene_objects=set(), registry=registry
    )  # scene objects are set per task below
    if args.client == "mock":
        client = MockVLMClient(response=_default_mock_response)
    else:
        client_kwargs: dict[str, Any] = {"model": args.model}
        if args.base_url:
            client_kwargs["base_url"] = args.base_url
        if args.env_file:
            client_kwargs["env_path"] = args.env_file
        if args.api_key:
            client_kwargs["api_key"] = args.api_key
        client = DashScopeVLMClient(**client_kwargs)
    json_mode = not args.no_json_mode
    planner = InitialPlanner(client, max_tokens=args.max_tokens, json_mode=json_mode)
    direct_planner = DirectPlanner(client, max_tokens=args.max_tokens)
    repairer = PlanRepairer(client, max_tokens=args.max_tokens, json_mode=json_mode)
    logger = EpisodeLogger(output_path)

    summaries = []
    for task in scenarios:
        scene_objects = set(task["objects"])
        validator.scene_objects = scene_objects
        for seed in range(args.seed_offset, args.seed_offset + args.seeds):
            if (task["task_id"], seed) in completed_keys:
                continue
            summary = collect_one(
                task,
                seed,
                planner=planner,
                direct_planner=direct_planner,
                repairer=repairer,
                validator=validator,
                logger=logger,
                repair_groups=args.repair_groups,
            )
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
