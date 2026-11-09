"""Fixed waypoint controller using target bindings and robot proprioception.

Completion means waypoint completion, NEVER verified object grasp/success.
No object poses, rewards or simulator task targets are read by this controller.
"""

import numpy as np

from ch3.execution.skill_contract import SkillRequest


class FixedPickPlaceController:
    backend = "fixed_multibody_waypoints"

    def __init__(self, env, *, pick_contact_offset=.015):
        if not np.isfinite(pick_contact_offset) or not .015 <= pick_contact_offset <= .04:
            raise ValueError('pick contact offset outside supported calibration range')
        self.env = env
        self.pick_contact_offset = float(pick_contact_offset)

    def retract_open(self, *, max_steps=80):
        """Bounded vertical reset for an authorized PRE-CONTACT interruption only.

        Caller must check that no descent/contact/gripper closure occurred.
        Workspace heuristic, not a collision-free motion guarantee.
        """
        if max_steps < 1:
            raise ValueError("positive step budget required")
        origin = np.asarray(self.env.get_endeff_pos(), float)
        if origin.shape != (3,) or not np.isfinite(origin).all() or not (.4 <= origin[1] <= .95 and abs(origin[0]) <= .3 and 0 < origin[2] <= .35):
            raise ValueError("retraction origin outside workspace")
        target = origin.copy()
        target[2] = min(.30, max(.20, origin[2] + .06))
        for step in range(1, max_steps + 1):
            action = np.r_[np.clip(10 * (target - self.env.get_endeff_pos()), -1, 1), -1.]
            _, _, terminated, truncated, _ = self.env.step(action)
            if terminated or truncated:
                return {"completed": False, "reason": "episode_ended", "steps": step}
            if np.linalg.norm(self.env.get_endeff_pos() - target) < .015:
                return {"completed": True, "reason": "vertical_reset_completed", "steps": step}
        return {"completed": False, "reason": "reset_timeout", "steps": max_steps}

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
                      (point + [0, 0, self.pick_contact_offset], -1, 80),
                      (point + [0, 0, self.pick_contact_offset], 1, 35),
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
            final_hand = np.asarray(self.env.get_endeff_pos(), dtype=float)
            trace.append({"phase": index, "steps": steps, "waypoint_reached": bool(reached),
                          "target_position": np.asarray(target).tolist(),
                          "final_hand_position": final_hand.tolist(),
                          "position_error": (final_hand - target).tolist(),
                          "binding_position": point.tolist(),
                          "binding_source": binding.source,
                          "observation_id": request.observation_id})
            dwell_completed = index != 2 or steps == limit
            if not reached or not dwell_completed:
                return {"completed": False, "reason": "waypoint_timeout", "steps": total, "trace": trace}
        return {"completed": True, "reason": "waypoints_completed_not_grasp_verified",
                "steps": total, "trace": trace}
