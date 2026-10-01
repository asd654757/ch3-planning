"""Fixed waypoint controller using target bindings and robot proprioception.

Completion means waypoint completion, NEVER verified object grasp/success.
No object poses, rewards or simulator task targets are read by this controller.
"""

import numpy as np

from ch3.execution.skill_contract import SkillRequest


class FixedPickPlaceController:
    backend = "fixed_multibody_waypoints"

    def __init__(self, env):
        self.env = env

    def execute(self, request: SkillRequest, *, max_steps: int = 260):
        if request.backend != self.backend or request.arm != "right":
            raise ValueError("unsupported backend or arm")
        if request.skill not in {"pick", "place"}:
            raise ValueError("unsupported skill")
        if max_steps < 1:
            raise ValueError("positive step budget required")
        binding = request.object_binding if request.skill == "pick" else request.target_binding
        if binding is None or binding.coordinate_frame != "mujoco_world":
            raise ValueError("missing destination or coordinate transform")
        if binding.source != "visual_estimate":
            raise ValueError("visual binding required")
        if not request.observation_id or binding.observation_id != request.observation_id:
            raise ValueError("stale target binding")
        point = np.asarray(binding.position, dtype=float)
        # Conservative task workspace; not a collision safety guarantee.
        if point.shape != (3,) or not np.isfinite(point).all() or not (-.3 <= point[0] <= .3 and .4 <= point[1] <= .95 and 0 <= point[2] <= .12):
            raise ValueError("target outside controller workspace")
        if request.skill == "pick":
            phases = [(point + [0, 0, .12], -1, 80),
                      (point + [0, 0, .015], -1, 80),
                      (point + [0, 0, .015], 1, 35),
                      (point + [0, 0, .15], 1, 65)]
        else:
            phases = [(point + [0, 0, .15], 1, 80),
                      (point + [0, 0, .045], 1, 80),
                      (point + [0, 0, .045], -1, 35),
                      (point + [0, 0, .15], -1, 65)]
        trace, total = [], 0
        for index, (target, grip, limit) in enumerate(phases):
            reached = False
            steps = 0
            for _ in range(limit):
                if total >= max_steps:
                    break
                hand = np.asarray(self.env.get_endeff_pos())
                action = np.r_[np.clip(10 * (target - hand), -1, 1), grip]
                _, _, terminated, truncated, _ = self.env.step(action)
                steps += 1
                total += 1
                reached = np.linalg.norm(np.asarray(self.env.get_endeff_pos()) - target) < .015
                if terminated or truncated:
                    return {"completed": False, "reason": "episode_ended", "steps": total, "trace": trace}
                if reached and index != 2:
                    break
            trace.append({"phase": index, "steps": steps, "waypoint_reached": bool(reached)})
            dwell_completed = index != 2 or steps == limit
            if not reached or not dwell_completed:
                return {"completed": False, "reason": "waypoint_timeout", "steps": total, "trace": trace}
        return {"completed": True, "reason": "waypoints_completed_not_grasp_verified",
                "steps": total, "trace": trace}
