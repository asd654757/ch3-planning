#!/usr/bin/env python3
"""Execution-state recovery benchmark.

Unlike plan-corruption pressure, the corrupted artifact is the *world state*
after a frozen plan prefix has executed.  Open-loop replays the nominal suffix;
state-aware arms receive the observed prefix-final state.  Repaired plans are
evaluated by executing only their remaining suffix from that observed state.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.goal.goal_checker import goal_satisfied
from ch3.logger.episode_logger import EpisodeLogger
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.validator.state_validator import simulate_plan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.validator.result import ValidationResult
from ch3.vlm.client import DashScopeVLMClient, VLMResponse
from ch3.vlm.collector import make_record, world_state_from_task
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.planner import PlanGeneration
from ch3.vlm.repair import PlanRepairer, RepairGeneration, required_transports
from scripts.repair_pressure import load_frozen_sources

SCHEMA_VERSION = "2026-09-14-state-recovery-v1"
DEFAULT_BENCHMARK = "state_recovery_v1"
PERTURBATIONS = (
    "nominal_state",
    "grasp_failure",
    "object_displacement",
    "wrong_held_object",
)
STATE_ARMS = (
    "OPEN_LOOP",
    "R2_NO_STATE",
    "R2_STATE",
    "ROUTED",
    "R1_FROM_STATE",
)


def _observed_state(
    *,
    task: Mapping[str, Any],
    source_plan: ModelPlan,
    validator: Validator,
    initial_state: WorldState,
    perturbation: str,
) -> tuple[int, list[Any], WorldState]:
    """Return (executed prefix length, prefix actions, observed state)."""
    rule = task["prefix_length_rule"]
    if rule == "full":
        prefix_len = len(source_plan.actions)
    elif rule == "first":
        prefix_len = min(1, len(source_plan.actions))
    elif rule == "none":
        prefix_len = 0
    else:
        raise ValueError(f"Unknown prefix_length_rule: {rule}")
    prefix = [a.model_copy(deep=True) for a in source_plan.actions[:prefix_len]]
    if prefix:
        _ok, _bad, _code, _msg, state = simulate_plan(
            ModelPlan(actions=prefix), initial_state, validator.valid_targets
        )
    else:
        state = initial_state.copy()
    family = task["task_family"]

    if perturbation == "nominal_state":
        return prefix_len, prefix, state

    if perturbation == "grasp_failure":
        if family == "pick_place":
            # The pick action ran but did not establish holding.
            target_obj = prefix[-1].object_id if prefix else None
            if target_obj:
                state.at[target_obj] = "table"
            state.holding.clear()
        elif family == "push":
            obj = source_plan.actions[0].object_id
            state.at[obj] = initial_state.at.get(obj, state.table_id)
            state.pushed.discard(obj)
        elif family == "press":
            obj = source_plan.actions[0].object_id
            state.pressed.discard(obj)
        else:
            raise ValueError(family)
        return prefix_len, prefix, state

    if perturbation == "object_displacement":
        # The planned effect completed, then the environment reset it.
        for goal_fact in task["goal"]["facts"]:
            if not isinstance(goal_fact, str) or not goal_fact.endswith(")"):
                continue
            if goal_fact.startswith(("on(", "pushed_to(")):
                inner = goal_fact[goal_fact.index("(") + 1:-1]
                obj, _target = [x.strip() for x in inner.split(",", 1)]
                if obj in state.objects:
                    state.at[obj] = initial_state.at.get(obj, state.table_id)
                    state.pushed.discard(obj)
                    state.holding.pop(next((a for a, o in state.holding.items() if o == obj), ""), None)
            elif goal_fact.startswith("pressed("):
                obj = goal_fact[len("pressed("):-1]
                state.pressed.discard(obj)
        return prefix_len, prefix, state

    if perturbation == "wrong_held_object":
        decoy = task["decoy_object"]
        arm = task["wrong_held_arm"]
        if family == "pick_place":
            if not prefix:
                raise ValueError("pick_place wrong_held_object requires a prefix")
            target_obj = prefix[-1].object_id
            state.holding[arm] = decoy
            state.at[target_obj] = "table"
        else:
            state.holding[arm] = decoy
        return prefix_len, prefix, state

    raise ValueError(f"Unknown perturbation: {perturbation}")


def _observed_validation(
    *,
    source_plan: ModelPlan,
    prefix: list[Any],
    state: WorldState,
    task: Mapping[str, Any],
) -> ValidationResult:
    valid = all(
        isinstance(f, str) and f in (state.facts() | state.empty_hand_facts({"left", "right"}))
        for f in task["goal"]["facts"]
    )
    if valid:
        return ValidationResult(
            valid=True,
            validated_prefix=[a.model_copy(deep=True) for a in prefix],
            final_state=state.copy(),
        )
    if len(prefix) < len(source_plan.actions):
        next_action = source_plan.actions[len(prefix)]
        # Map execution anomalies to existing state-layer codes; the message
        # preserves the observed-state provenance.
        from ch3.errors import ErrorCode
        code = ErrorCode.OBJECT_NOT_HELD if next_action.skill.value == "place" else ErrorCode.ARM_NOT_EMPTY
        return ValidationResult(
            valid=False,
            first_invalid_step=next_action.step_id,
            error_code=code,
            message=f"execution-state mismatch before step {next_action.step_id}",
            layer="execution_state",
            validated_prefix=[a.model_copy(deep=True) for a in prefix],
            final_state=state.copy(),
        )
    from ch3.errors import ErrorCode
    return ValidationResult(
        valid=False,
        first_invalid_step=None,
        error_code=ErrorCode.STATE_TRANSITION_ERROR,
        message="executed plan did not retain its planned goal effect",
        layer="execution_state",
        validated_prefix=[a.model_copy(deep=True) for a in prefix],
        final_state=state.copy(),
    )


def _no_state_validation(validation: ValidationResult) -> ValidationResult:
    return dataclasses.replace(validation, final_state=None)


def _evaluate_suffix(
    *,
    plan: Optional[ModelPlan],
    prefix_len: int,
    task: Mapping[str, Any],
    validator: Validator,
    observed_state: WorldState,
) -> tuple[bool, bool, bool, Optional[WorldState]]:
    """Evaluate only actions after the already-executed prefix."""
    if plan is None or len(plan.actions) < prefix_len:
        return False, False, False, None
    suffix = plan.actions[prefix_len:]
    if not suffix:
        result = ValidationResult(valid=True, final_state=observed_state.copy())
    else:
        renumbered = [
            action.model_copy(update={"step_id": i + 1})
            for i, action in enumerate(suffix)
        ]
        result = validator.validate(ModelPlan(actions=renumbered), observed_state)
    goal = GoalSpec.model_validate(task["goal"])
    final_state = result.final_state if result.valid else None
    satisfied = bool(result.valid and goal_satisfied(final_state or observed_state, goal, validator.registry.arms))
    return bool(result.valid), satisfied, bool(result.valid and not satisfied), final_state


def _minimal_suffix_actions(task: Mapping[str, Any], state: WorldState) -> int:
    transports = required_transports(task, state)
    total = sum(
        1 if entry.get("currently_held") or entry["kind"] in {"push", "press"} else 2
        for entry in transports
    )
    # A wrong held object must first be safely put down.
    if state.holding:
        total += 1
    return total


def _open_loop_generation(source_plan: ModelPlan) -> RepairGeneration:
    return RepairGeneration(
        repair_mode="OPEN_LOOP",
        plan=source_plan.model_copy(deep=True),
        response=VLMResponse(content="", model="open-loop-replay", latency_ms=0),
        accepted=True,
        prompt="",
        prompt_id="open_loop_replay",
        prompt_hash="open_loop_replay",
    )


def _routed_state_repair(
    *,
    repairer: PlanRepairer,
    task: Mapping[str, Any],
    stress_generation: PlanGeneration,
    validation: ValidationResult,
    validator: Validator,
    observed_state: WorldState,
    seed: int,
    temperature: float,
) -> tuple[RepairGeneration, dict[str, Any]]:
    r2 = repairer.repair(
        repair_mode="R2",
        task=task,
        initial_generation=stress_generation,
        validation=validation,
        initial_state=observed_state,
        seed=seed,
        temperature=temperature,
    )
    r2_valid, r2_goal, r2_pbw, _state = _evaluate_suffix(
        plan=r2.plan,
        prefix_len=len(validation.validated_prefix),
        task=task,
        validator=validator,
        observed_state=observed_state,
    )
    info: dict[str, Any] = {
        "route_taken": ["R2"],
        "fallback_triggered": False,
        "r2_valid": r2_valid,
        "r2_goal_ok": r2_goal,
        "r2_pbw": r2_pbw,
    }
    if r2_valid and r2_goal:
        return r2, info
    r1 = repairer.repair(
        repair_mode="R1_FROM_STATE",
        task=task,
        initial_generation=stress_generation,
        validation=validation,
        initial_state=world_state_from_task(task),
        seed=seed,
        temperature=temperature,
    )
    info["route_taken"].append("R1_FROM_STATE")
    info["fallback_triggered"] = True
    info["fallback_mode"] = "R1_FROM_STATE"
    return r1, info


def run_state_slice(
    *,
    task: Mapping[str, Any],
    seed: int,
    source_plan: ModelPlan,
    repairer: PlanRepairer,
    validator: Validator,
    logger: EpisodeLogger,
    arms: Iterable[str] = STATE_ARMS,
    temperature: float = 0.3,
) -> dict[str, Any]:
    initial_state = world_state_from_task(task)
    prefix_len, prefix, observed_state = _observed_state(
        task=task,
        source_plan=source_plan,
        validator=validator,
        initial_state=initial_state,
        perturbation=task["perturbation_type"],
    )
    observed_validation = _observed_validation(
        source_plan=source_plan,
        prefix=prefix,
        state=observed_state,
        task=task,
    )
    stress_generation = PlanGeneration(
        source_plan.model_copy(deep=True),
        None,
        None,
        "",
        "frozen_state_recovery_source",
        "frozen_state_recovery_source",
    )
    requested = list(arms)
    repairs: dict[str, dict[str, Any]] = {}
    model_calls = 0
    for arm in requested:
        if arm == "OPEN_LOOP":
            repaired = _open_loop_generation(source_plan)
        elif arm == "R2_NO_STATE":
            repaired = repairer.repair(
                repair_mode="R2",
                task=task,
                initial_generation=stress_generation,
                validation=_no_state_validation(observed_validation),
                initial_state=initial_state,
                seed=seed,
                temperature=temperature,
            )
            model_calls += 1
        elif arm == "R2_STATE":
            repaired = repairer.repair(
                repair_mode="R2",
                task=task,
                initial_generation=stress_generation,
                validation=observed_validation,
                initial_state=observed_state,
                seed=seed,
                temperature=temperature,
            )
            model_calls += 1
        elif arm == "ROUTED":
            repaired, route_info = _routed_state_repair(
                repairer=repairer,
                task=task,
                stress_generation=stress_generation,
                validation=observed_validation,
                validator=validator,
                observed_state=observed_state,
                seed=seed,
                temperature=temperature,
            )
            model_calls += len(route_info["route_taken"])
        elif arm == "R1_FROM_STATE":
            repaired = repairer.repair(
                repair_mode="R1_FROM_STATE",
                task=task,
                initial_generation=stress_generation,
                validation=observed_validation,
                initial_state=initial_state,
                seed=seed,
                temperature=temperature,
            )
            model_calls += 1
        else:
            raise ValueError(f"Unknown state-recovery arm: {arm}")

        valid, goal_ok, pbw, final_state = _evaluate_suffix(
            plan=repaired.plan,
            prefix_len=prefix_len,
            task=task,
            validator=validator,
            observed_state=observed_state,
        )
        prefix_mutation = (
            repaired.plan is None
            or len(repaired.plan.actions) < prefix_len
            or [a.model_dump() for a in repaired.plan.actions[:prefix_len]]
            != [a.model_dump() for a in prefix]
        )
        suffix_len = max(0, len(repaired.plan.actions) - prefix_len) if repaired.plan else 0
        minimal = _minimal_suffix_actions(task, observed_state)
        record = make_record(
            task=task,
            seed=seed,
            record_type="state_recovery",
            baseline=arm,
            generation=repaired,
            plan=repaired.plan,
            validation=observed_validation,
            goal_ok=goal_ok,
            pass_but_wrong=pbw,
        )
        record.update({
            "schema_version": SCHEMA_VERSION,
            "benchmark": DEFAULT_BENCHMARK,
            "perturbation_type": task["perturbation_type"],
            "source_task_id": task["source_task_id"],
            "executed_prefix_step_ids": [a.step_id for a in prefix],
            "observed_state_facts": sorted(
                observed_state.facts() | observed_state.empty_hand_facts({"left", "right"})
            ),
            "recovery_success": bool(valid and goal_ok and not prefix_mutation),
            "prefix_mutation": prefix_mutation,
            "suffix_action_count": suffix_len,
            "minimal_suffix_action_count": minimal,
            "unnecessary_action_count": max(0, suffix_len - minimal),
            "model_calls": 0 if arm == "OPEN_LOOP" else (len(route_info["route_taken"]) if arm == "ROUTED" else 1),
            "arm_model_calls": model_calls,
            "stress_validation": {
                "valid": observed_validation.valid,
                "first_invalid_step": observed_validation.first_invalid_step,
                "error_code": observed_validation.error_code.value if observed_validation.error_code else None,
                "layer": observed_validation.layer,
                "message": observed_validation.message,
            },
            **({} if arm != "ROUTED" else route_info),
        })
        logger.append(record)
        repairs[arm] = {
            "valid": valid,
            "goal_satisfied": goal_ok,
            "pass_but_wrong": pbw,
            "recovery_success": bool(valid and goal_ok and not prefix_mutation),
            "prefix_mutation": prefix_mutation,
            "model_calls": record["model_calls"],
        }
    return {
        "task_id": task["task_id"],
        "source_task_id": task["source_task_id"],
        "seed": seed,
        "perturbation_type": task["perturbation_type"],
        "prefix_len": prefix_len,
        "arms": requested,
        "model_calls": model_calls,
        "repairs": repairs,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", default="data/scenarios/state_recovery_tasks_v1.jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument("--source-collection", required=True)
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--env-file",
        default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--client", choices=["real", "mock"], default="real")
    parser.add_argument("--no-json-mode", action="store_true")
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--arms", nargs="+", choices=STATE_ARMS, default=STATE_ARMS)
    parser.add_argument("--perturbations", nargs="+", choices=PERTURBATIONS, default=PERTURBATIONS)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--task-ids", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-image", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    from ch3.vlm.collector import load_scenarios
    output_path = Path(args.output)
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite: {output_path}. Use a new timestamp.")
    scenarios = load_scenarios(args.scenarios)
    scenarios = [t for t in scenarios if t["perturbation_type"] in set(args.perturbations)]
    if args.task_ids:
        wanted = set(args.task_ids)
        scenarios = [t for t in scenarios if t["task_id"] in wanted]
    if args.limit is not None:
        scenarios = scenarios[: args.limit]
    if args.no_image:
        scenarios = [{**t, "image_path": None} for t in scenarios]
    registry = CapabilityRegistry.from_yaml(args.registry)
    validator = Validator(scene_objects=set(), registry=registry)
    if args.client == "mock":
        client = MockVLMClient(response="mock")
    else:
        kwargs: dict[str, Any] = {"model": args.model}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        if args.env_file:
            kwargs["env_path"] = args.env_file
        if args.api_key:
            kwargs["api_key"] = args.api_key
        client = DashScopeVLMClient(**kwargs)
    frozen = load_frozen_sources(args.source_collection)
    print(f"[state-recovery] loaded {len(frozen)} frozen source plans", flush=True)
    repairer = PlanRepairer(
        client,
        max_tokens=args.max_tokens,
        json_mode=not args.no_json_mode,
    )
    logger = EpisodeLogger(output_path)
    started = time.time()
    summaries = []
    for task in scenarios:
        validator.scene_objects = set(task["objects"])
        for seed in range(args.seed_offset, args.seed_offset + args.seeds):
            source_plan = frozen.get((task["source_task_id"], seed))
            if source_plan is None:
                print(f"[state-recovery] missing source {task['source_task_id']} seed={seed}", flush=True)
                continue
            print(f"[state-recovery] {task['task_id']} seed={seed}", flush=True)
            summary = run_state_slice(
                task=task,
                seed=seed,
                source_plan=source_plan,
                repairer=repairer,
                validator=validator,
                logger=logger,
                arms=args.arms,
                temperature=args.temperature,
            )
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(json.dumps({
        "completed_slices": len(summaries),
        "elapsed_s": round(time.time() - started, 3),
        "output": str(output_path),
        "arms": args.arms,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
