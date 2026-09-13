#!/usr/bin/env python3
"""Repair-pressure benchmark runner.

This runner deliberately separates *task difficulty* from *repair pressure*:
for every feasible task and seed, it asks the VLM for one valid baseline plan,
then injects deterministic invalid prefixes/suffixes into that plan.  The
corrupted plan is fed to the frozen one-shot R0/R1/R2 repair protocol.  Thus
every mode sees the same controllable error distribution and we can compare
CRR / GSR_after_repair without depending on the initial planner making the
same mistake across arms.

Output is JSONL and is deliberately not overwritten unless ``--append`` or
``--resume`` is explicit.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.logger.episode_logger import EpisodeLogger
from ch3.protocols.frozen import REPAIR_GROUPS
from ch3.schema.model_plan import ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.client import VLMResponse
from ch3.vlm.collector import (
    SHARED_BASELINE,
    evaluate_plan,
    load_scenarios,
    make_record,
    response_summary,
    world_state_from_task,
)
from ch3.vlm.mock import MockVLMClient
from ch3.vlm.parser import plan_to_dict
from ch3.vlm.planner import InitialPlanner, PlanGeneration
from ch3.vlm.prompts import PromptLibrary
from ch3.vlm.repair import PlanRepairer
from ch3.vlm.repair import RepairGeneration

PRESSURE_TYPES = (
    "duplicate_pick_after_prefix",
    "unknown_object_after_prefix",
    "invalid_target_after_prefix",
    "place_before_pick",
    "repeat_pick_after_valid_plan",
    # Skill-family-agnostic pressures used by MultiSkill-IF.  The legacy
    # names above remain frozen for old collections and regression tests.
    "duplicate_skill_after_prefix",
    "unknown_object_after_generic_prefix",
    "invalid_action_after_prefix",
)

SCHEMA_VERSION = "2026-09-11-repair-pressure-v1"
DEFAULT_BENCHMARK = "legacy_pick_place"


def _renumber(actions: Iterable[ModelPlanAction]) -> ModelPlan:
    return ModelPlan(
        actions=[
            action.model_copy(update={"step_id": i + 1})
            for i, action in enumerate(actions)
        ]
    )


def _first(plan: ModelPlan, skill: Skill) -> Optional[ModelPlanAction]:
    return next((action for action in plan.actions if action.skill == skill), None)


def _with_unknown_object(action: ModelPlanAction) -> ModelPlanAction:
    return action.model_copy(update={"object_id": "__unknown_object__"})


def _with_unknown_target(action: ModelPlanAction) -> ModelPlanAction:
    return action.model_copy(update={"target_id": "__unknown_target__"})


def _first_skill_action(plan: ModelPlan) -> ModelPlanAction:
    """Return the first executable action of any registered skill family."""
    return plan.actions[0]


def build_stress_plan(
    valid_plan: ModelPlan,
    task: Mapping[str, Any],
    pressure_type: str,
) -> ModelPlan:
    """Build a deterministic invalid plan from a known-valid baseline plan."""
    actions = list(valid_plan.actions)
    first_pick = _first(valid_plan, Skill.PICK)
    first_place = _first(valid_plan, Skill.PLACE)

    if pressure_type == "duplicate_pick_after_prefix":
        if first_pick is None:
            raise ValueError("valid plan has no pick action")
        idx = actions.index(first_pick)
        # Keep any actions before the first pick valid, then force ARM_NOT_EMPTY.
        prefix = actions[: idx + 1]
        return _renumber([*prefix, first_pick.model_copy(), *actions[idx + 1 :]])

    if pressure_type == "unknown_object_after_prefix":
        if first_pick is None:
            raise ValueError("valid plan has no pick action")
        idx = actions.index(first_pick)
        prefix = actions[: idx + 1]
        return _renumber(
            [*prefix, _with_unknown_object(first_pick.model_copy()), *actions[idx + 1 :]]
        )

    if pressure_type == "invalid_target_after_prefix":
        if first_pick is None:
            raise ValueError("valid plan has no pick action")
        idx = actions.index(first_pick)
        prefix = actions[: idx + 1]
        place_to_unknown = first_pick.model_copy(
            update={"skill": Skill.PLACE, "target_id": "__unknown_target__"}
        )
        return _renumber(
            [*prefix, place_to_unknown, *actions[idx + 1 :]]
        )

    if pressure_type == "place_before_pick":
        if first_pick is None or first_place is None:
            raise ValueError("valid plan lacks pick/place")
        # Reversing an actual pick/place pair creates a state-only error.
        remaining = [a for i, a in enumerate(actions) if i != actions.index(first_place)]
        return _renumber([first_place.model_copy(), first_pick.model_copy(), *remaining[1:]])

    if pressure_type == "repeat_pick_after_valid_plan":
        if first_pick is None:
            raise ValueError("valid plan has no pick action")
        # Keep the whole valid sequence, then violate the final state.
        return _renumber([*actions, first_pick.model_copy()])

    if pressure_type == "duplicate_skill_after_prefix":
        first = _first_skill_action(valid_plan)
        # For non-prehensile skills, repeating push/press is legal or idempotent
        # under the state simulator.  An unheld place after the valid prefix is
        # a skill-family-agnostic state error with the same repair difficulty.
        second = (
            first.model_copy()
            if first.skill == Skill.PICK
            else first.model_copy(update={"skill": Skill.PLACE, "target_id": "table"})
        )
        return _renumber([first.model_copy(), second, *actions[1:]])

    if pressure_type == "unknown_object_after_generic_prefix":
        first = _first_skill_action(valid_plan)
        return _renumber(
            [first.model_copy(), _with_unknown_object(first.model_copy()), *actions[1:]]
        )

    if pressure_type == "invalid_action_after_prefix":
        first = _first_skill_action(valid_plan)
        if first.skill == Skill.PRESS:
            # Press requires an empty arm and leaves it empty, so a following
            # place deterministically fails in the state layer (OBJECT_NOT_HELD).
            illegal = first.model_copy(
                update={"skill": Skill.PLACE, "target_id": "table"}
            )
        elif first.skill in {Skill.PUSH, Skill.PLACE}:
            # Preserve the family while corrupting the target ID.
            illegal = _with_unknown_target(first.model_copy())
        else:
            # A pick/place baseline starts with a pick; request a place to an
            # unknown target after that pick.  This preserves the usual
            # invalid_target semantics while remaining schema-valid.
            illegal = first.model_copy(
                update={"skill": Skill.PLACE, "target_id": "__unknown_target__"}
            )
        return _renumber([first.model_copy(), illegal, *actions[1:]])

    raise ValueError(f"Unknown pressure type: {pressure_type}")


def stress_plan_summary(
    plan: ModelPlan,
    validation: Any,
) -> dict[str, Any]:
    return {
        "stress_plan": plan_to_dict(plan),
        "stress_valid": bool(validation.valid),
        "stress_error_code": validation.error_code.value if validation.error_code else None,
        "stress_error_layer": validation.layer,
        "stress_error_message": validation.message,
        "stress_first_invalid_step": validation.first_invalid_step,
        "stress_validated_prefix": [a.model_dump() for a in validation.validated_prefix],
    }


def repair_pressure_record(
    *,
    task: Mapping[str, Any],
    seed: int,
    pressure_type: str,
    source_generation: Any,
    stress_plan: ModelPlan,
    stress_validation: Any,
    repaired: Any,
    repaired_plan: Optional[ModelPlan],
    repaired_validation: Any,
    repaired_goal_ok: bool,
    repaired_pbw: bool,
    benchmark: str = DEFAULT_BENCHMARK,
) -> dict[str, Any]:
    # make_record preserves raw output, prompt, tokens, latency and refusal fields.
    record = make_record(
        task=task,
        seed=seed,
        record_type="repair",
        baseline="B2B",
        generation=repaired,
        plan=repaired_plan,
        validation=repaired_validation,
        goal_ok=repaired_goal_ok,
        pass_but_wrong=repaired_pbw,
    )
    record.update(
        {
            "schema_version": SCHEMA_VERSION,
            "benchmark": benchmark,
            "record_type": "repair_pressure",
            "pressure_type": pressure_type,
            "pressure_source_plan": plan_to_dict(source_generation.plan),
            "pressure_source_valid": True,
            "pressure_source_goal_satisfied": True,
            **stress_plan_summary(stress_plan, stress_validation),
        }
    )
    return record


def pressure_source_record(
    *,
    task: Mapping[str, Any],
    seed: int,
    source_generation: Any,
    validation: Any,
    goal_ok: bool,
    pbw: bool,
    benchmark: str = DEFAULT_BENCHMARK,
) -> dict[str, Any]:
    record = make_record(
        task=task,
        seed=seed,
        record_type="pressure_source",
        baseline=SHARED_BASELINE,
        generation=source_generation,
        plan=source_generation.plan,
        validation=validation,
        goal_ok=goal_ok,
        pass_but_wrong=pbw,
    )
    record["schema_version"] = SCHEMA_VERSION
    record["benchmark"] = benchmark
    record["record_type"] = "pressure_source"
    record["pressure_source_valid"] = bool(validation and validation.valid)
    record["pressure_source_goal_satisfied"] = bool(goal_ok)
    return record


def load_frozen_sources(path: str | Path) -> dict[tuple[str, int], ModelPlan]:
    """Reuse baseline plans recorded in a prior pressure collection.

    Every ``repair_pressure`` row stores the (uncorrupted) baseline plan in
    ``pressure_source_plan``.  Keying by (task_id, seed) lets a follow-up run
    skip the 1 baseline call per slice and compare repairs against the exact
    frozen baselines.
    """
    sources: dict[tuple[str, int], ModelPlan] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            key = (r.get("task_id"), r.get("seed"))
            plan_dict = r.get("pressure_source_plan")
            if key in sources or not plan_dict:
                continue
            sources[key] = ModelPlan(**plan_dict)
    return sources


ROUTED_GROUP = "ROUTED"
ROUTED_REPAIR_GROUPS: dict[str, str] = {
    **REPAIR_GROUPS,
    ROUTED_GROUP: "Adaptive routed repair (state-aware R2 first, R1 fallback on "
                  "validation or goal failure)",
}


def _prefix_completes_goal(
    *,
    task: Mapping[str, Any],
    validation: Any,
) -> bool:
    """Return whether the validated prefix already satisfies the full goal.

    This is a program-side deterministic rule for the common corruption
    pattern ``valid complete plan + redundant invalid tail``.  It must not
    fire while an arm is holding an object, because an executable final plan
    should not leave a manipulated object unplaced.
    """
    final_state = validation.final_state
    if final_state is None or not validation.validated_prefix:
        return False
    if final_state.holding:
        return False
    prefix_final = final_state.facts() | final_state.empty_hand_facts({"left", "right"})
    goal_facts = task.get("goal", {}).get("facts", [])
    return all(isinstance(fact, str) and fact in prefix_final for fact in goal_facts)


def _deterministic_truncation(
    validation: Any,
) -> RepairGeneration:
    """Build a no-model-call repair by retaining the validated prefix."""
    prefix_plan = ModelPlan(
        actions=[
            action.model_copy(deep=True)
            for action in validation.validated_prefix
        ]
    )
    return RepairGeneration(
        repair_mode=ROUTED_GROUP,
        plan=prefix_plan,
        response=VLMResponse(
            content="",
            model="deterministic-truncation",
            latency_ms=0,
        ),
        accepted=True,
        prompt="",
        prompt_id="deterministic_truncation",
        prompt_hash="deterministic_truncation",
        locked_prefix_step_ids=tuple(
            action.step_id for action in validation.validated_prefix
        ),
        merged_with_prefix=True,
    )


def routed_repair(
    *,
    repairer: PlanRepairer,
    task: Mapping[str, Any],
    initial_generation: PlanGeneration,
    validation: Any,
    initial_state: WorldState,
    validator: Validator,
    seed: Optional[int],
    temperature: float,
) -> tuple[Any, dict[str, Any]]:
    """Error-aware routed repair (docs/method_design_20260912.md, section 3).

    Route: state-aware R2 suffix first; fall back to R1 full replan when the
    R2 result fails validation or does not satisfy the goal (VGF).  When the
    executed prefix already satisfies every goal fact and both arms are empty,
    deterministic truncation removes the invalid tail without a model call.
    Returns ``(final_generation, route_info)``; the caller records both
    attempts' outcome for system-level analysis.
    """
    if _prefix_completes_goal(task=task, validation=validation):
        info: dict[str, Any] = {
            "route_taken": ["DETERMINISTIC_TRUNCATION"],
            "fallback_triggered": False,
            "r2_skipped": True,
            "r2_valid": None,
            "r2_goal_ok": None,
            "r2_pbw": None,
            "deterministic_truncation": True,
        }
        return _deterministic_truncation(validation), info

    r2 = repairer.repair(
        repair_mode="R2",
        task=task,
        initial_generation=initial_generation,
        validation=validation,
        initial_state=initial_state,
        seed=seed,
        temperature=temperature,
    )
    r2_validation, r2_goal, r2_pbw = evaluate_plan(
        r2.plan, task, validator, initial_state
    )
    r2_ok = bool(r2_validation and r2_validation.valid) and bool(r2_goal)
    info: dict[str, Any] = {
        "route_taken": ["R2"],
        "fallback_triggered": False,
        "r2_valid": bool(r2_validation and r2_validation.valid),
        "r2_goal_ok": bool(r2_goal),
        "r2_pbw": bool(r2_pbw),
    }
    if r2_ok:
        return r2, info

    # A plan-time fallback may return a full replacement plan.  But when the
    # validated prefix represents an already-executed robot prefix, R1 must
    # not repeat those actions.  R1_FROM_STATE replans from prefix_final_state
    # and merges the returned suffix behind the executed prefix.
    fallback_mode = (
        "R1_FROM_STATE"
        if validation.final_state is not None and validation.validated_prefix
        else "R1"
    )
    info["route_taken"].append(fallback_mode)
    info["fallback_triggered"] = True
    r1 = repairer.repair(
        repair_mode=fallback_mode,
        task=task,
        initial_generation=initial_generation,
        validation=validation,
        initial_state=initial_state,
        seed=seed,
        temperature=temperature,
    )
    info["fallback_mode"] = fallback_mode
    return r1, info


def _relabeled_repair(
    repaired: Any,
    repair_mode: str,
) -> Any:
    """Return a repair generation relabeled for record aggregation."""
    if repair_mode == ROUTED_GROUP and repaired.repair_mode != ROUTED_GROUP:
        return dataclasses.replace(repaired, repair_mode=ROUTED_GROUP)
    return repaired


def run_pressure_slice(
    task: Mapping[str, Any],
    seed: int,
    *,
    planner: InitialPlanner,
    repairer: PlanRepairer,
    validator: Validator,
    logger: EpisodeLogger,
    pressure_types: Iterable[str] = PRESSURE_TYPES,
    repair_groups: Iterable[str] = ("R0", "R1", "R2"),
    benchmark: str = DEFAULT_BENCHMARK,
    initial_temperature: float = 0.7,
    repair_temperature: float = 0.3,
    source_plan: Optional[ModelPlan] = None,
) -> dict[str, Any]:
    """Run one task/seed pressure slice. The baseline must be valid.

    ``source_plan`` reuses a frozen baseline plan (no planner call).  When it
    is None the baseline is generated fresh via ``planner.plan``.
    """
    requested_pressure = list(pressure_types)
    for pressure_type in requested_pressure:
        if pressure_type not in PRESSURE_TYPES:
            raise ValueError(f"Unknown pressure type: {pressure_type}")
    requested_repairs = list(repair_groups)
    for repair_mode in requested_repairs:
        if repair_mode not in ROUTED_REPAIR_GROUPS:
            raise ValueError(f"Unknown repair group: {repair_mode}")

    initial_state = world_state_from_task(task)
    if source_plan is not None:
        # Frozen baseline reuse: validate the stored plan, no model call.
        source = PlanGeneration(source_plan, None, None, None, None, None)
    else:
        source = planner.plan(task, seed=seed, temperature=initial_temperature)
    source_validation, source_goal, source_pbw = evaluate_plan(
        source.plan, task, validator, initial_state
    )

    # A pressure source must be a complete task solution, not merely a
    # state-valid plan.  A state-valid but goal-failing baseline would turn a
    # repair benchmark into an unintended planning benchmark: even the
    # "valid prefix" could be far short of the remaining task goal.
    if (
        source_validation is None
        or not source_validation.valid
        or source.plan is None
        or not source_goal
    ):
        source_record = pressure_source_record(
            task=task,
            seed=seed,
            source_generation=source,
            validation=source_validation,
            goal_ok=source_goal,
            pbw=source_pbw,
            benchmark=benchmark,
        )
        logger.append(source_record)
        return {
            "task_id": task["task_id"],
            "seed": seed,
            "baseline_valid": False,
            "pressure_calls": 0,
            "benchmark": benchmark,
            "repairs": {},
        }

    repairs: dict[str, dict[str, Any]] = {}
    for pressure_type in requested_pressure:
        stress_plan = build_stress_plan(source.plan, task, pressure_type)
        stress_validation = validator.validate(stress_plan, initial_state)
        if stress_validation.valid:
            # This is a benchmark construction error, not a model failure.
            raise ValueError(
                f"Stress plan unexpectedly valid: {task['task_id']} / {pressure_type}"
            )
        # Critical fairness rule: repairers must see the corrupted plan as the
        # "original plan".  Passing the valid source plan would leak the answer
        # to R1/R2 through the repair prompt.
        stress_generation = PlanGeneration(
            stress_plan,
            source.response,
            None,
            source.prompt,
            source.prompt_id,
            source.prompt_hash,
        )
        for repair_mode in requested_repairs:
            route_info: dict[str, Any] = {}
            if repair_mode == ROUTED_GROUP:
                repaired, route_info = routed_repair(
                    repairer=repairer,
                    task=task,
                    initial_generation=stress_generation,
                    validation=stress_validation,
                    initial_state=initial_state,
                    validator=validator,
                    seed=seed,
                    temperature=repair_temperature,
                )
                # The routed generation may come from the R1 fallback; label
                # the record as ROUTED and keep per-route fields for analysis.
                repaired = _relabeled_repair(repaired, repair_mode)
            else:
                repaired = repairer.repair(
                    repair_mode=repair_mode,
                    task=task,
                    initial_generation=stress_generation,
                    validation=stress_validation,
                    initial_state=initial_state,
                    seed=seed,
                    temperature=repair_temperature,
                )
            repaired_validation, repaired_goal, repaired_pbw = evaluate_plan(
                repaired.plan, task, validator, initial_state
            )
            record = repair_pressure_record(
                task=task,
                seed=seed,
                pressure_type=pressure_type,
                source_generation=source,
                stress_plan=stress_plan,
                stress_validation=stress_validation,
                repaired=repaired,
                repaired_plan=repaired.plan,
                repaired_validation=repaired_validation,
                repaired_goal_ok=repaired_goal,
                repaired_pbw=repaired_pbw,
                benchmark=benchmark,
            )
            if repair_mode == ROUTED_GROUP:
                record["repair_mode"] = ROUTED_GROUP
                record["baseline"] = ROUTED_GROUP
                record.update(route_info)
            logger.append(record)
            repairs.setdefault(pressure_type, {})[repair_mode] = {
                "accepted": repaired.accepted,
                "reject_reason": repaired.reject_reason,
                "valid": bool(repaired_validation and repaired_validation.valid),
                "goal_satisfied": repaired_goal,
                "pass_but_wrong": repaired_pbw,
                **route_info,
            }

    return {
        "task_id": task["task_id"],
        "seed": seed,
        "baseline_valid": True,
        "pressure_calls": len(requested_pressure) * len(requested_repairs),
        "benchmark": benchmark,
        "repairs": repairs,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        default="config/scenarios_pilot.jsonl",
        help="Feasible-task JSONL file",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Pressure collection JSONL output on the data disk",
    )
    parser.add_argument("--registry", default="config/capability_registry.yaml")
    parser.add_argument(
        "--benchmark",
        default=DEFAULT_BENCHMARK,
        help="Lineage marker stored in every pressure record and summary.",
    )
    parser.add_argument("--model", default="qwen3-vl-flash")
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL override",
    )
    parser.add_argument(
        "--env-file",
        default="/root/autodl-tmp/metaworld-smolvla/qwen-dialogue-control/.env",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument(
        "--client",
        choices=["real", "mock"],
        default="real",
    )
    parser.add_argument("--no-json-mode", action="store_true")
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--seed-offset", type=int, default=0)
    parser.add_argument("--pressure-types", nargs="+", choices=PRESSURE_TYPES, default=PRESSURE_TYPES)
    parser.add_argument(
        "--repair-groups",
        nargs="+",
        choices=sorted(ROUTED_REPAIR_GROUPS),
        default=sorted(REPAIR_GROUPS),
    )
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--repair-temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--task-ids", nargs="*")
    parser.add_argument("--difficulties", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-image", action="store_true")
    parser.add_argument(
        "--source-collection",
        default=None,
        help="Reuse frozen baseline plans from a prior pressure JSONL "
             "(keyed by task_id/seed) instead of calling the planner.",
    )
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.seeds < 1:
        raise ValueError("--seeds must be >= 1")
    output_path = Path(args.output)
    if output_path.exists() and not args.append and not args.resume:
        raise FileExistsError(
            f"Refusing to overwrite existing pressure collection: {output_path}. "
            "Use a new timestamped output, --append, or --resume."
        )

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
    validator = Validator(scene_objects=set(), registry=registry)
    if args.client == "mock":
        # This deterministic callback is useful for smoke tests.  Formal runs
        # must use --client real so the baseline and repairs are real VLM data.
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

    frozen_sources: dict[tuple[str, int], ModelPlan] = {}
    if args.source_collection:
        frozen_sources = load_frozen_sources(args.source_collection)
        print(
            f"[pressure] loaded {len(frozen_sources)} frozen baseline plans "
            f"from {args.source_collection}",
            flush=True,
        )

    json_mode = not args.no_json_mode
    planner = InitialPlanner(client, max_tokens=args.max_tokens, json_mode=json_mode)
    repairer = PlanRepairer(client, max_tokens=args.max_tokens, json_mode=json_mode)
    logger = EpisodeLogger(output_path)
    started = time.time()
    summaries = []
    for task in scenarios:
        validator.scene_objects = set(task["objects"])
        for seed in range(args.seed_offset, args.seed_offset + args.seeds):
            print(f"[pressure] {task['task_id']} seed={seed}", flush=True)
            summary = run_pressure_slice(
                task,
                seed,
                planner=planner,
                repairer=repairer,
                validator=validator,
                logger=logger,
                pressure_types=args.pressure_types,
                repair_groups=args.repair_groups,
                initial_temperature=args.temperature,
                repair_temperature=args.repair_temperature,
                source_plan=frozen_sources.get((task["task_id"], seed)),
                benchmark=args.benchmark,
            )
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    print(
        json.dumps(
            {
                "completed_slices": len(summaries),
                "valid_baseline_slices": sum(s["baseline_valid"] for s in summaries),
                "elapsed_s": round(time.time() - started, 3),
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
