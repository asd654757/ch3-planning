"""Single-puck, same-episode state-feedback recovery feasibility pilot.

This is not a visual estimator or a multi-object benchmark. State is obtained
from the simulator. Paired methods use the same seed; recovery never resets.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ch3.capability.registry import load_registry
from ch3.compiler.executable_plan import compile_plan
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.repair.observed_recovery import recover_from_observation
from ch3.schema.model_plan import GoalSpec, ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from scripts.sim_execute_plan_smoke import build_smoke_plan


def observe(executor):
    raw = executor._state()
    state = WorldState.table_scene({"red_cube_0", "tray_1"})
    distance = float(np.linalg.norm(raw["puck_pos"] - raw["target_pos"]))
    # Single-puck identity is known; this heuristic is not a contact sensor.
    if executor._is_holding(raw):
        state.holding["right"] = "red_cube_0"
    elif distance <= executor.goal_tolerance:
        state.at["red_cube_0"] = "tray_1"
    executor._held_symbolic_object = state.holding.get("right")
    return state, distance


def run_case(seed, perturbation, method, budget):
    registry = load_registry()
    # The adapter represents one physical arm, not interchangeable dual arms.
    registry._arms = {"right"}
    validator = Validator({"red_cube_0", "tray_1"}, registry)
    goal = GoalSpec(facts=["on(red_cube_0, tray_1)"])
    plan = build_smoke_plan()
    executable = compile_plan(plan, registry)
    executor = MetaWorldPlanExecutor(seed=seed, strict_release=True)
    trace = []
    try:
        initial = executor.reset()
        failed_index = 0 if perturbation == "grasp_timeout" else 1
        for index, action in enumerate(executable.steps):
            result = executor.execute_step(action, max_steps=1 if index == failed_index else budget)
            trace.append({"phase": "initial", "step_id": action.step_id,
                          "success": result.success, "steps": result.steps})
            if not result.success:
                failed_index = index
                break
        observed, before_distance = observe(executor)
        initial_failed = not trace[-1]["success"]
        recovery_reason = "not_requested"
        if initial_failed and method == "RETRY_SAME_SUFFIX":
            for action in executable.steps[failed_index:]:
                try:
                    result = executor.execute_step(action, max_steps=budget)
                except RuntimeError as exc:
                    recovery_reason = str(exc)
                    break
                trace.append({"phase": "recovery", "step_id": action.step_id,
                              "success": result.success, "steps": result.steps})
                if not result.success:
                    break
        elif initial_failed and method == "OBSERVED_SEARCH":
            def execute_and_observe(before, action):
                # Candidate validation already happened; compile only this action.
                result = executor.execute_step(
                    compile_plan(ModelPlan(actions=[action]), registry).steps[0],
                    max_steps=budget,
                )
                trace.append({"phase": "recovery", "action": action.model_dump(mode="json"),
                              "success": result.success, "steps": result.steps})
                state, _ = observe(executor)
                return state if result.success else None

            outcome = recover_from_observation(
                observed_state=observed, goal=goal, validator=validator,
                allowed_skills={"pick", "place"}, symbolic_only=False,
                observe_after_action=execute_and_observe,
            )
            recovery_reason = outcome.reason
        final, distance = observe(executor)
        return {"seed": seed, "perturbation": perturbation, "method": method,
                "initial_scene": initial, "initial_failed": initial_failed,
                "observed_facts_after_failure": sorted(observed.facts()),
                "before_distance": before_distance, "final_distance": distance,
                "success": final.at["red_cube_0"] == "tray_1" and not final.holding,
                "recovery_reason": recovery_reason, "trace": trace,
                "total_control_steps": sum(item["steps"] for item in trace),
                "reset_count": 1, "vlm_calls": 0}
    finally:
        executor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seeds < 1 or args.budget < 1:
        parser.error("seeds and budget must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        for seed in range(args.seeds):
            for perturbation in ("grasp_timeout", "place_timeout"):
                for method in ("NO_RECOVERY", "RETRY_SAME_SUFFIX", "OBSERVED_SEARCH"):
                    row = run_case(seed, perturbation, method, args.budget)
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    print(json.dumps({k: row[k] for k in
                                      ("seed", "perturbation", "method", "success", "recovery_reason")}), flush=True)
    print(json.dumps({"completed_at_utc": datetime.now(timezone.utc).isoformat(),
                      "cases": args.seeds * 6, "output": str(args.output),
                      "protocol": "single_puck_same_episode_simulator_state_feedback"}), flush=True)


if __name__ == "__main__":
    main()
