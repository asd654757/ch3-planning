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
    # Lift height alone misses a grasp that has been brought down to a table
    # target. Do not report the goal as achieved while the gripper is closed.
    gripper_open = raw["gripper_distance"] > executor.gripper_closed_threshold
    near_hand = np.linalg.norm(raw["puck_pos"] - raw["hand_pos"]) < 0.075
    if executor._is_holding(raw) or (not gripper_open and near_hand):
        state.holding["right"] = "red_cube_0"
    elif gripper_open and distance <= executor.goal_tolerance:
        state.at["red_cube_0"] = "tray_1"
    executor._held_symbolic_object = state.holding.get("right")
    return state, distance


def stable_goal_check(executor, steps=10):
    """Identical terminal observation window for every comparison method."""
    previous = executor._state()["puck_pos"].copy()
    stable = True
    completed = 0
    for _ in range(steps):
        try:
            _, _, terminated, truncated, _ = executor._env._env.step(
                np.array([0.0, 0.0, 0.0, 0.0])
            )
        except ValueError as exc:
            if "reset the env manually" not in str(exc):
                raise
            return False, completed
        completed += 1
        raw = executor._state()
        stable = stable and (
            np.linalg.norm(raw["puck_pos"] - raw["target_pos"]) <= executor.goal_tolerance
            and raw["gripper_distance"] > executor.gripper_closed_threshold
            and np.linalg.norm(raw["puck_pos"] - previous) < 0.001
        )
        previous = raw["puck_pos"].copy()
        if terminated or truncated:
            stable = False
            break
    return bool(stable and completed == steps), completed


def run_case(seed, perturbation, method, budget, supported_table_goal=False,
             phased_grasp=False):
    registry = load_registry()
    # The adapter represents one physical arm, not interchangeable dual arms.
    registry._arms = {"right"}
    validator = Validator({"red_cube_0", "tray_1"}, registry)
    goal = GoalSpec(facts=["on(red_cube_0, tray_1)"])
    plan = build_smoke_plan()
    executable = compile_plan(plan, registry)
    executor = MetaWorldPlanExecutor(
        seed=seed, strict_release=True,
        # The target and low-level primitive are fixed before the episode for
        # every perturbation and recovery method. This keeps the comparison
        # about recovery, rather than a controller or task-goal change.
        supported_table_goal=supported_table_goal,
        phased_grasp=phased_grasp,
    )
    trace = []
    try:
        initial = executor.reset()
        initial_target_pos = list(initial["target_pos"])
        failed_index = 0 if perturbation == "grasp_timeout" else 1
        disturbance_steps = 0
        perturbation_applied = False
        for index, action in enumerate(executable.steps):
            result = executor.execute_step(action, max_steps=(
                1 if index == failed_index and perturbation != "post_grasp_slip" else budget
            ))
            trace.append({"phase": "initial", "step_id": action.step_id,
                          "success": result.success, "steps": result.steps})
            if not result.success:
                failed_index = index
                break
            if perturbation == "post_grasp_slip" and index == 0:
                # Release by control, without teleportation or episode reset.
                # Allow the gripper to open and the released puck to settle
                # before deciding whether the disturbance actually occurred.
                for _ in range(30):
                    _, _, terminated, truncated, _ = executor._env._env.step(
                        np.array([0.0, 0.0, 0.0, -1.0])
                    )
                    disturbance_steps += 1
                    if terminated or truncated:
                        break
                    raw = executor._state()
                    if (raw["gripper_distance"] > executor.gripper_closed_threshold
                            and raw["puck_pos"][2] < executor.lifted_height_threshold):
                        break
                slipped, _ = observe(executor)
                perturbation_applied = (not slipped.holding
                                        and slipped.at["red_cube_0"] == "table")
                post_failure_puck_pos = executor._state()["puck_pos"].tolist()
                post_failure_gripper_distance = executor._state()["gripper_distance"]
                failed_index = 1
                break
        if perturbation != "post_grasp_slip":
            current = executor._state()
            post_failure_puck_pos = current["puck_pos"].tolist()
            post_failure_gripper_distance = current["gripper_distance"]
        observed, before_distance = observe(executor)
        initial_failed = perturbation_applied or not trace[-1]["success"]
        actual_failed_step = failed_index + 1 if initial_failed else None
        recovery_reason = "not_requested"
        recovery_accepted = False
        if perturbation == "post_grasp_slip" and not perturbation_applied:
            recovery_reason = "perturbation_not_realized"
        if initial_failed and method == "RETRY_SAME_SUFFIX" and (perturbation != "post_grasp_slip" or perturbation_applied):
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
        elif initial_failed and method == "OBSERVED_SEARCH" and (perturbation != "post_grasp_slip" or perturbation_applied):
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
            recovery_accepted = outcome.accepted
        stable_success, observation_steps = stable_goal_check(executor)
        final, distance = observe(executor)
        return {"seed": seed, "perturbation": perturbation, "method": method,
                "initial_scene": initial, "initial_failed": initial_failed,
                "actual_failed_step": actual_failed_step,
                "observed_facts_after_failure": sorted(observed.facts()),
                "before_distance": before_distance, "final_distance": distance,
                "success": stable_success,
                "instantaneous_goal_satisfied": final.at["red_cube_0"] == "tray_1" and not final.holding,
                "success_definition": "10_steps_target_distance_open_gripper_and_position_stability",
                "perturbation_applied": perturbation_applied if perturbation == "post_grasp_slip" else initial_failed,
                "disturbance_steps": disturbance_steps,
                "initial_target_pos": initial_target_pos,
                "post_failure_puck_pos": post_failure_puck_pos,
                "post_failure_gripper_distance": post_failure_gripper_distance,
                "terminal_observation_steps": observation_steps,
                "recovery_accepted": recovery_accepted if method == "OBSERVED_SEARCH" else None,
                "recovery_reason": recovery_reason, "trace": trace,
                "supported_table_goal": supported_table_goal,
                "target_height_changed_after_grasp": False,
                "strict_release": True,
                "phased_grasp_after_failure": phased_grasp,
                "final_puck_pos": executor._state()["puck_pos"].tolist(),
                "final_gripper_distance": executor._state()["gripper_distance"],
                "total_control_steps": sum(item["steps"] for item in trace) + disturbance_steps + observation_steps,
                "reset_count": 1, "vlm_calls": 0}
    finally:
        executor.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--budget", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supported-table-goal", action="store_true",
                        help="Controlled table placement, not native MetaWorld success protocol.")
    parser.add_argument("--phased-grasp", action="store_true",
                        help="Use a fixed approach/descend/close/lift primitive after failure.")
    args = parser.parse_args()
    if args.seeds < 1 or args.budget < 1:
        parser.error("seeds and budget must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        for seed in range(args.seeds):
            for perturbation in ("grasp_timeout", "place_timeout", "post_grasp_slip"):
                for method in ("NO_RECOVERY", "RETRY_SAME_SUFFIX", "OBSERVED_SEARCH"):
                    row = run_case(seed, perturbation, method, args.budget,
                                   args.supported_table_goal, args.phased_grasp)
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    print(json.dumps({k: row[k] for k in
                                      ("seed", "perturbation", "method", "success", "recovery_reason")}), flush=True)
    print(json.dumps({"completed_at_utc": datetime.now(timezone.utc).isoformat(),
                      "cases": args.seeds * 9, "output": str(args.output),
                      "supported_table_goal": args.supported_table_goal,
                      "phased_grasp_after_failure": args.phased_grasp,
                      "protocol": "single_puck_same_episode_simulator_state_feedback"}), flush=True)


if __name__ == "__main__":
    main()
