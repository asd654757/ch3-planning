"""MetaWorld execution adapter for validated task-level plans.

This module keeps the trust boundary explicit: callers must provide an
``ExecutablePlan`` produced by ``ch3.compiler.executable_plan.compile_plan``.
The adapter does not re-plan, repair, or invent actions.  It only maps the
already-validated grasp/place primitives onto the MetaWorld expert policy.

The current smoke adapter is deliberately scoped to a single-object
``metaworld-pick-place-v3`` task.  Symbolic object and target ids are aliases
to the simulator's single puck and goal.  Later adapters can replace the expert
policy with AD-Flow policies while preserving the same interface.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from ch3.compiler.executable_plan import ExecutablePlan, ExecutableStep


class MetaWorldExecutionError(RuntimeError):
    """Raised when an executable step cannot be safely mapped to simulation."""


@dataclass
class MetaWorldStepResult:
    step_id: int
    policy_id: str
    primitive: str
    args: dict[str, Any]
    success: bool
    steps: int
    elapsed_s: float
    final_puck_pos: list[float]
    final_hand_pos: list[float]
    target_pos: list[float]
    final_puck_target_distance: float
    min_puck_target_distance: float
    gripper_distance: float
    terminated: bool
    truncated: bool
    info_success: bool


@dataclass
class MetaWorldPlanResult:
    success: bool
    steps: list[MetaWorldStepResult]
    final_puck_pos: list[float]
    target_pos: list[float]
    final_puck_target_distance: float
    elapsed_s: float


class MetaWorldPlanExecutor:
    """Execute a compiled task-level plan in one-object MetaWorld simulation."""

    _SUPPORTED_POLICY_IDS = {
        "adflow_grasp",
        "adflow_place",
        "metaworld_push",
        "metaworld_button_press",
    }

    def __init__(
        self,
        *,
        task: str = "metaworld-pick-place-v3",
        observation_size: int = 224,
        goal_tolerance: float = 0.08,
        push_goal_tolerance: float = 0.07,
        gripper_closed_threshold: float = 0.73,
        lifted_height_threshold: float = 0.04,
        seed: int | None = None,
        strict_release: bool = False,
    ) -> None:
        self.task = task
        self.strict_release = strict_release
        self.observation_size = observation_size
        self.goal_tolerance = goal_tolerance
        self.push_goal_tolerance = push_goal_tolerance
        self.gripper_closed_threshold = gripper_closed_threshold
        self.lifted_height_threshold = lifted_height_threshold
        self._held_symbolic_object: str | None = None
        self._env = None
        if seed is not None:
            np.random.seed(seed)

    def _make_env(self):
        # Import is deferred so the repository's light-weight test environment
        # does not require the simulation stack.
        os.environ.setdefault("MUJOCO_GL", "egl")
        from lerobot.envs.metaworld import MetaworldEnv

        return MetaworldEnv(
            task=self.task,
            observation_width=self.observation_size,
            observation_height=self.observation_size,
            obs_type="pixels_agent_pos",
        )

    def reset(self) -> dict[str, Any]:
        self._env = self._make_env()
        self._env.reset()
        self._held_symbolic_object = None
        raw = self._env._env._get_obs()
        state = self._read_state(raw)
        return {
            "initial_puck_pos": state["puck_pos"].tolist(),
            "initial_hand_pos": state["hand_pos"].tolist(),
            "target_pos": state["target_pos"].tolist(),
        }

    def render_frame(self) -> np.ndarray:
        """Render the current camera frame using the same orientation as obs."""
        if self._env is None:
            raise MetaWorldExecutionError("MetaWorld executor has not been reset")
        frame = np.asarray(self._env._env.render(), dtype=np.uint8).copy()
        # The lerobot adapter flips corner2 when building pixel observations;
        # use the same convention so saved feedback frames match the visual
        # observations used by pixel-based policies.
        if getattr(self._env, "camera_name", None) == "corner2":
            frame = np.flip(frame, (0, 1))
        return frame

    def save_frame(self, path: str | Any) -> None:
        """Save the current camera frame to a PNG without overwriting."""
        from pathlib import Path

        frame_path = Path(path)
        if frame_path.exists():
            raise FileExistsError(f"refusing to overwrite frame: {frame_path}")
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - simulation-only path
            raise RuntimeError("Pillow is required to save visual feedback") from exc
        Image.fromarray(self.render_frame()).save(frame_path, format="PNG")

    def _read_state(self, raw: np.ndarray) -> dict[str, Any]:
        if self._env is None:
            raise MetaWorldExecutionError("MetaWorld executor has not been reset")
        target_pos = np.asarray(self._env._env.unwrapped._target_pos, dtype=float)
        return {
            "hand_pos": np.asarray(raw[:3], dtype=float),
            "gripper_distance": float(raw[3]),
            "puck_pos": np.asarray(raw[4:7], dtype=float),
            "target_pos": target_pos,
            "raw": raw,
        }

    def _state(self) -> dict[str, Any]:
        if self._env is None:
            raise MetaWorldExecutionError("MetaWorld executor has not been reset")
        return self._read_state(self._env._env._get_obs())

    def _base_result(self, step: ExecutableStep) -> dict[str, Any]:
        state = self._state()
        return {
            "step_id": step.step_id,
            "policy_id": step.policy_id,
            "primitive": step.primitive,
            "args": dict(step.args),
            "steps": 0,
            "elapsed_s": 0.0,
            "final_puck_pos": state["puck_pos"].tolist(),
            "final_hand_pos": state["hand_pos"].tolist(),
            "target_pos": state["target_pos"].tolist(),
            "final_puck_target_distance": float(
                np.linalg.norm(state["puck_pos"] - state["target_pos"])
            ),
            "min_puck_target_distance": float("inf"),
            "gripper_distance": state["gripper_distance"],
            "terminated": False,
            "truncated": False,
            "info_success": False,
        }

    def _expert_action(self, raw: np.ndarray) -> np.ndarray:
        if self._env is None:
            raise MetaWorldExecutionError("MetaWorld executor has not been reset")
        return np.asarray(self._env.expert_policy.get_action(raw), dtype=float)

    def _is_holding(self, state: dict[str, Any]) -> bool:
        return (
            state["gripper_distance"] <= self.gripper_closed_threshold
            and state["puck_pos"][2] >= self.lifted_height_threshold
        )

    def _run_grasp(self, step: ExecutableStep, max_steps: int) -> MetaWorldStepResult:
        if self._held_symbolic_object is not None:
            raise MetaWorldExecutionError(
                f"cannot execute grasp for {step.args.get('object_id')!r}: "
                f"another symbolic object is already held"
            )
        result = self._base_result(step)
        started = time.time()
        for _ in range(max_steps):
            state = self._state()
            action = self._expert_action(state["raw"])
            _, _, terminated, truncated, _ = self._env._env.step(action)
            result["steps"] += 1
            result["terminated"] = bool(terminated)
            result["truncated"] = bool(truncated)

            state = self._state()
            distance = float(np.linalg.norm(state["puck_pos"] - state["target_pos"]))
            result["min_puck_target_distance"] = min(
                result["min_puck_target_distance"], distance
            )
            result["final_puck_pos"] = state["puck_pos"].tolist()
            result["final_hand_pos"] = state["hand_pos"].tolist()
            result["gripper_distance"] = state["gripper_distance"]
            if self._is_holding(state):
                result["success"] = True
                self._held_symbolic_object = str(step.args["object_id"])
                break
            if terminated or truncated:
                result["success"] = False
                break
        else:
            result["success"] = False

        result["elapsed_s"] = time.time() - started
        return MetaWorldStepResult(**result)

    def _run_press(self, step: ExecutableStep, max_steps: int) -> MetaWorldStepResult:
        if self._held_symbolic_object is not None:
            raise MetaWorldExecutionError(
                f"cannot execute press for {step.args.get('object_id')!r}: "
                f"another symbolic object is already held"
            )
        result = self._base_result(step)
        started = time.time()
        for _ in range(max_steps):
            state = self._state()
            action = self._expert_action(state["raw"])
            _, _, terminated, truncated, info = self._env._env.step(action)
            result["steps"] += 1
            result["terminated"] = bool(terminated)
            result["truncated"] = bool(truncated)

            state = self._state()
            distance = float(np.linalg.norm(state["puck_pos"] - state["target_pos"]))
            result["min_puck_target_distance"] = min(
                result["min_puck_target_distance"], distance
            )
            result["final_puck_pos"] = state["puck_pos"].tolist()
            result["final_hand_pos"] = state["hand_pos"].tolist()
            result["info_success"] = bool(info.get("success", info.get("is_success", False)))
            if result["info_success"]:
                result["success"] = True
                break
            if terminated or truncated:
                result["success"] = False
                break
        else:
            result["success"] = False

        result["elapsed_s"] = time.time() - started
        return MetaWorldStepResult(**result)

    def _run_place(self, step: ExecutableStep, max_steps: int) -> MetaWorldStepResult:
        symbolic_object = str(step.args["object_id"])
        if self._held_symbolic_object != symbolic_object:
            raise MetaWorldExecutionError(
                f"place expects held object {symbolic_object!r}, "
                f"but executor holds {self._held_symbolic_object!r}"
            )
        result = self._base_result(step)
        started = time.time()
        releasing = False
        stable_steps = 0
        for _ in range(max_steps):
            state = self._state()
            previous_puck = state["puck_pos"].copy()
            action = self._expert_action(state["raw"])
            if self.strict_release:
                releasing = releasing or float(np.linalg.norm(
                    state["puck_pos"] - state["target_pos"]
                )) <= self.goal_tolerance
                if releasing:
                    action = np.array([0.0, 0.0, 0.0, -1.0])
            _, _, terminated, truncated, info = self._env._env.step(action)
            result["steps"] += 1
            result["terminated"] = bool(terminated)
            result["truncated"] = bool(truncated)

            state = self._state()
            distance = float(np.linalg.norm(state["puck_pos"] - state["target_pos"]))
            result["min_puck_target_distance"] = min(
                result["min_puck_target_distance"], distance
            )
            result["final_puck_pos"] = state["puck_pos"].tolist()
            result["final_hand_pos"] = state["hand_pos"].tolist()
            result["gripper_distance"] = state["gripper_distance"]
            result["info_success"] = bool(info.get("success", info.get("is_success", False)))
            stable_steps = stable_steps + 1 if (
                distance <= self.goal_tolerance
                and state["gripper_distance"] > self.gripper_closed_threshold
                and float(np.linalg.norm(state["puck_pos"] - previous_puck)) < 0.001
            ) else 0
            success = (stable_steps >= 10 if self.strict_release else
                       result["info_success"] or distance <= self.goal_tolerance)
            if success:
                result["success"] = True
                self._held_symbolic_object = None
                break
            if terminated or truncated:
                result["success"] = False
                break
        else:
            result["success"] = False

        result["elapsed_s"] = time.time() - started
        return MetaWorldStepResult(**result)

    def _run_push(self, step: ExecutableStep, max_steps: int) -> MetaWorldStepResult:
        if self._held_symbolic_object is not None:
            raise MetaWorldExecutionError(
                f"cannot execute push for {step.args.get('object_id')!r}: "
                f"another symbolic object is already held"
            )
        result = self._base_result(step)
        started = time.time()
        for _ in range(max_steps):
            state = self._state()
            action = self._expert_action(state["raw"])
            _, _, terminated, truncated, info = self._env._env.step(action)
            result["steps"] += 1
            result["terminated"] = bool(terminated)
            result["truncated"] = bool(truncated)

            state = self._state()
            distance = float(np.linalg.norm(state["puck_pos"] - state["target_pos"]))
            result["min_puck_target_distance"] = min(
                result["min_puck_target_distance"], distance
            )
            result["final_puck_pos"] = state["puck_pos"].tolist()
            result["final_hand_pos"] = state["hand_pos"].tolist()
            result["info_success"] = bool(info.get("success", info.get("is_success", False)))
            if result["info_success"] or distance <= self.push_goal_tolerance:
                result["success"] = True
                break
            if terminated or truncated:
                result["success"] = False
                break
        else:
            result["success"] = False

        result["elapsed_s"] = time.time() - started
        return MetaWorldStepResult(**result)

    def execute_step(self, step: ExecutableStep, *, max_steps: int = 300) -> MetaWorldStepResult:
        if step.policy_id not in self._SUPPORTED_POLICY_IDS:
            raise MetaWorldExecutionError(
                f"policy {step.policy_id!r} is not registered for MetaWorld execution"
            )
        if step.primitive == "grasp":
            return self._run_grasp(step, max_steps)
        if step.primitive == "place":
            return self._run_place(step, max_steps)
        if step.primitive == "push":
            return self._run_push(step, max_steps)
        if step.primitive == "press":
            return self._run_press(step, max_steps)
        raise MetaWorldExecutionError(f"unsupported primitive: {step.primitive!r}")

    def execute_plan(self, plan: ExecutablePlan, *, max_steps_per_primitive: int = 300) -> MetaWorldPlanResult:
        started = time.time()
        results: list[MetaWorldStepResult] = []
        for step in plan.steps:
            result = self.execute_step(step, max_steps=max_steps_per_primitive)
            results.append(result)
            if not result.success:
                break
        final_state = self._state()
        final_distance = float(np.linalg.norm(final_state["puck_pos"] - final_state["target_pos"]))
        return MetaWorldPlanResult(
            success=bool(results) and all(r.success for r in results),
            steps=results,
            final_puck_pos=final_state["puck_pos"].tolist(),
            target_pos=final_state["target_pos"].tolist(),
            final_puck_target_distance=final_distance,
            elapsed_s=time.time() - started,
        )

    def result_to_dict(self, result: MetaWorldPlanResult) -> dict[str, Any]:
        payload = asdict(result)
        payload["task"] = self.task
        payload["record_type"] = "sim_metaworld_plan_execution"
        return payload

    def close(self) -> None:
        if self._env is not None:
            try:
                self._env.close()
            finally:
                self._env = None
