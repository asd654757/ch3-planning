#!/usr/bin/env python3
"""Run a validated symbolic ch3 plan through the MetaWorld smoke adapter."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.capability.registry import load_registry
from ch3.compiler.executable_plan import compile_plan
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.schema.model_plan import Arm, ModelPlan, ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator


def build_smoke_plan() -> ModelPlan:
    return ModelPlan(
        actions=[
            ModelPlanAction(
                step_id=1,
                skill=Skill.PICK,
                object_id="red_cube_0",
                arm=Arm.RIGHT,
            ),
            ModelPlanAction(
                step_id=2,
                skill=Skill.PLACE,
                object_id="red_cube_0",
                target_id="tray_1",
                arm=Arm.RIGHT,
            ),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--max-steps-per-primitive", type=int, default=300)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--output-dir", type=Path, default=Path("data/collections"))
    args = parser.parse_args()

    registry = load_registry("config/capability_registry.yaml")
    scene_objects = {"red_cube_0", "blue_cube_0", "tray_0", "tray_1", "box_0"}
    initial_state = WorldState.table_scene(scene_objects)
    plan = build_smoke_plan()

    validator = Validator(scene_objects=scene_objects, registry=registry)
    validation = validator.validate(plan, initial_state)
    if not validation.valid:
        raise RuntimeError(
            f"smoke plan rejected by validator: {validation.error_code} {validation.message}"
        )

    executable_plan = compile_plan(plan, registry)
    episodes = []
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"sim_metaworld_plan_execution_{stamp}.json"

    for episode in range(args.episodes):
        executor = MetaWorldPlanExecutor(
            observation_size=args.observation_size,
            seed=episode,
        )
        print(f"[sim-plan] episode={episode + 1}/{args.episodes} start", flush=True)
        try:
            reset_info = executor.reset()
            result = executor.execute_plan(
                executable_plan,
                max_steps_per_primitive=args.max_steps_per_primitive,
            )
            payload = executor.result_to_dict(result)
            payload.update(
                {
                    "episode": episode,
                    "model_plan": plan.model_dump(mode="json"),
                    "executable_plan": executable_plan.to_list(),
                    "validation": {
                        "valid": validation.valid,
                        "first_invalid_step": validation.first_invalid_step,
                        "error_code": validation.error_code,
                    },
                    "reset": reset_info,
                }
            )
            episodes.append(payload)
            print(
                f"[sim-plan] episode={episode + 1}/{args.episodes} "
                f"success={result.success} "
                f"step_results={[r.success for r in result.steps]} "
                f"distance={result.final_puck_target_distance:.4f}",
                flush=True,
            )
        finally:
            executor.close()

    summary = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_type": "sim_metaworld_plan_execution_summary",
        "episodes": args.episodes,
        "successes": sum(e["success"] for e in episodes),
        "success_rate": sum(e["success"] for e in episodes) / args.episodes,
        "output": str(output_path),
    }
    output_path.write_text(
        json.dumps({**summary, "episodes_detail": episodes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
