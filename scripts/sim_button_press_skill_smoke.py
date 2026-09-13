"""Run a symbolic press plan through validator/compiler and MetaWorld button-press-v3.

This smoke test checks the second task interface, not a benchmark.  The
symbolic ``button_0`` is an alias for the button object in
``metaworld-button-press-v3``.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ch3.capability.registry import load_registry
from ch3.compiler.executable_plan import compile_plan
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.goal.goal_checker import goal_satisfied
from ch3.schema.model_plan import Arm, GoalSpec, ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


def build_case() -> tuple[ModelPlan, GoalSpec, WorldState, Validator]:
    registry = load_registry()
    scene_objects = {"button_0"}
    initial_state = WorldState.table_scene(scene_objects)
    validator = Validator(
        scene_objects=scene_objects,
        registry=registry,
        special_targets=set(registry.special_targets),
    )
    plan = ModelPlan(
        actions=[
            ModelPlanAction(
                step_id=1,
                skill=Skill.PRESS,
                object_id="button_0",
                arm=Arm.RIGHT,
            )
        ]
    )
    goal = GoalSpec(facts=["pressed(button_0)"])
    return plan, goal, initial_state, validator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    plan, goal, initial_state, validator = build_case()
    validation = validator.validate(plan, initial_state)
    if not validation.valid:
        raise RuntimeError(f"press plan failed validation: {validation.message}")
    if not goal_satisfied(validation.final_state, goal, arms={"left", "right"}):
        raise RuntimeError("validated press plan did not satisfy the symbolic goal")

    executable = compile_plan(plan, validator.registry)
    records: list[dict] = []
    started = time.time()
    for episode in range(1, args.episodes + 1):
        print(f"[sim-press] episode={episode}/{args.episodes} start", flush=True)
        executor = MetaWorldPlanExecutor(
            task="metaworld-button-press-v3",
            observation_size=224,
            seed=args.seed + episode - 1,
        )
        sim_state = executor.reset()
        result = executor.execute_plan(executable, max_steps_per_primitive=args.max_steps)
        records.append(
            {
                "episode": episode,
                "success": result.success,
                "steps": [asdict(s) for s in result.steps],
                "final_puck_pos": result.final_puck_pos,
                "target_pos": result.target_pos,
                "final_distance": result.final_puck_target_distance,
                "sim_initial_state": sim_state,
            }
        )
        step_result = result.steps[-1] if result.steps else None
        print(
            f"[sim-press] episode={episode}/{args.episodes} "
            f"success={result.success} "
            f"steps={step_result.steps if step_result else 0} "
            f"distance={result.final_puck_target_distance:.4f}",
            flush=True,
        )

    successes = sum(r["success"] for r in records)
    summary = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_type": "sim_button_press_skill_smoke_summary",
        "simulation_task": "metaworld-button-press-v3",
        "skill": "press",
        "symbolic_validation_valid": validation.valid,
        "symbolic_goal_satisfied": goal_satisfied(
            validation.final_state,
            goal,
            arms={"left", "right"},
        ),
        "episodes": args.episodes,
        "max_steps_per_primitive": args.max_steps,
        "successes": successes,
        "success_rate": successes / args.episodes if args.episodes else None,
        "results": records,
        "elapsed_s": time.time() - started,
        "output": str(args.output),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
