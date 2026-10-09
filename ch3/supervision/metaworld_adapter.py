"""Single-episode state-feedback adapter; no learned policy or visual estimation.

Object ids alias the native puck/goal. Internal positions are an explicitly
privileged state observation, not evidence of unstructured visual perception.
"""
from __future__ import annotations

import json
from uuid import uuid4
import numpy as np

from ch3.capability.registry import CapabilityRegistry
from ch3.execution.metaworld_executor import MetaWorldPlanExecutor
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator
from .core import Evidence, Observation, Receipt, Supervisor, Task, Truth
from .runtime import Session

OBJECT = "red_cube_0"
TARGET = "tray_1"
GOAL = f"on({OBJECT}, {TARGET})"


def decode_state(raw, *, goal_tolerance, closed_threshold, lift_threshold):
    """Reconstruct occupancy from measurements, never command bookkeeping."""
    state = WorldState.table_scene({OBJECT, TARGET})
    opened = raw["gripper_distance"] > closed_threshold
    near_hand = np.linalg.norm(raw["puck_pos"] - raw["hand_pos"]) < 0.075
    held = not opened and (near_hand or raw["puck_pos"][2] >= lift_threshold)
    distance = float(np.linalg.norm(raw["puck_pos"] - raw["target_pos"]))
    if held:
        state.at.pop(OBJECT)
        state.holding["right"] = OBJECT
    elif opened and distance <= goal_tolerance:
        state.at[OBJECT] = TARGET
    return state


class MetaWorldObserver:
    def __init__(self, executor, *, settle_steps=10):
        self.executor = executor
        self.episode_id = uuid4().hex
        self.sequence = -1
        self.settle_steps = settle_steps

    def read(self):
        ex = self.executor
        raw = ex._state()
        state = decode_state(raw, goal_tolerance=ex.goal_tolerance,
            closed_threshold=ex.gripper_closed_threshold,
            lift_threshold=ex.lifted_height_threshold)
        stable = GOAL in state.facts()
        # Independent post-command stability window, with the gripper open.
        if stable:
            for _ in range(self.settle_steps):
                previous = raw["puck_pos"].copy()
                _, _, terminated, truncated, _ = ex._env._env.step(
                    np.array([0., 0., 0., -1.]))
                raw = ex._state()
                stable = stable and not (terminated or truncated) and (
                    np.linalg.norm(raw["puck_pos"] - previous) < 0.001)
                if terminated or truncated:
                    raise RuntimeError("episode ended during observation")
            state = decode_state(raw, goal_tolerance=ex.goal_tolerance,
                closed_threshold=ex.gripper_closed_threshold,
                lift_threshold=ex.lifted_height_threshold)
        # Synchronize legacy backend precondition with observed occupancy.
        ex._held_symbolic_object = state.holding.get("right")
        self.sequence += 1
        truth = Truth.TRUE if stable and GOAL in state.facts() else (
            Truth.UNKNOWN if GOAL in state.facts() else Truth.FALSE)
        return Observation(self.episode_id, self.sequence, state,
            {"right": state.holding.get("right")},
            {GOAL: Evidence(truth, "simulator_state_observer",
                           "privileged positions + gripper + stability window")})


class MetaWorldBackend:
    def __init__(self, executor, *, max_steps=300, interrupt_first_grasp=False):
        self.executor = executor
        self.max_steps = max_steps
        self.interrupt_first_grasp = interrupt_first_grasp
        self.results = []

    def execute(self, step, *, instruction, context, command_id):
        if step.source_skill not in {"pick", "place"} or step.args.get("arm") != "right":
            raise ValueError("single-arm pick/place backend")
        if step.args.get("object_id") != OBJECT or (
            step.source_skill == "place" and step.args.get("target_id") != TARGET):
            raise ValueError("unmapped physical object/target")
        budget = self.max_steps
        if self.interrupt_first_grasp and step.source_skill == "pick":
            self.interrupt_first_grasp = False
            budget = 1  # Actually execute one simulator step, then time out.
        result = self.executor.execute_step(step, max_steps=budget)
        from dataclasses import asdict
        self.results.append({"command_id": command_id, **asdict(result)})
        return Receipt("success" if result.success else "failed",
                       f"fixed_controller steps={result.steps}; budget={budget}")


class ScriptedSmokePlanner:
    """Adapter test double. Its calls are not VLM/API calls."""
    def generate(self, request, images):
        actions = []
        if request["held_objects"].get("right") != OBJECT:
            actions.append(dict(step_id=1, skill="pick", object_id=OBJECT, arm="right"))
        actions.append(dict(step_id=len(actions)+1, skill="place", object_id=OBJECT,
                            target_id=TARGET, arm="right"))
        return json.dumps({"actions": actions})


def build_session(planner=None, *, seed=0, interrupt_first_grasp=False, max_steps=300):
    # Restrict both planning and compilation to the physical backend's capability.
    registry = CapabilityRegistry({"arms": ["right"], "capabilities": {
        "pick": {"primitive": "grasp", "args": ["object_id", "arm"], "policy": "adflow_grasp"},
        "place": {"primitive": "place", "args": ["object_id", "target_id", "arm"], "policy": "adflow_place"}}})
    validator = Validator({OBJECT, TARGET}, registry, special_targets={"table"})
    ex = MetaWorldPlanExecutor(seed=seed, strict_release=True,
                              supported_table_goal=True, phased_grasp=True)
    try:
        ex.reset()
    except Exception:
        ex.close()
        raise
    session = Session(Supervisor(Task("Place the red cube on the target and release it", (GOAL,)),
        validator, planner or ScriptedSmokePlanner(), max_model_calls=6, max_commands=8),
        MetaWorldObserver(ex), MetaWorldBackend(ex, max_steps=max_steps,
            interrupt_first_grasp=interrupt_first_grasp), "simulator")
    return session
