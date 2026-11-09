"""Budgeted RGB recovery fixture. No model API, rewards, or truth in decisions.

The external case schedule controls sensor dropout. A recovery policy sees only
actual RGB/proprioception, receipts, command history and remaining resources.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import time

from ch3.repair.recovery_decision import Budget

CASES = ("normal", "temporary", "delayed", "persistent", "precontact", "released")


class ResourceLimit(RuntimeError):
    pass


class RecoveryFixture:
    def __init__(self, seed, case, output: Path):
        if case not in CASES:
            raise ValueError("unknown case")
        import numpy as np
        from metaworld.asset_path_utils import full_V3_path_for
        from ch3.execution.multiobject_scene import build_scene_xml, make_scene
        from ch3.execution.fixed_pickplace import FixedPickPlaceController
        from ch3.execution.skill_contract import BackendContract
        from ch3.execution.visual_geometry import fixed_mujoco_calibration
        self.np = np
        self.output = output
        output.mkdir(parents=True, exist_ok=False)
        self.tmp = tempfile.TemporaryDirectory()
        xml = Path(self.tmp.name)/"scene.xml"
        build_scene_xml(Path(full_V3_path_for("sawyer_xyz/sawyer_pick_place_v3.xml")), xml, hide_goal_marker=True)
        self.env = make_scene(xml, seed=seed)
        self.controller = FixedPickPlaceController(self.env)
        self.contract = BackendContract(self.controller.backend, frozenset({"pick", "place"}),
                                        frozenset({"right"}), "mujoco_world")
        self.calibration = fixed_mujoco_calibration(self.env.model, self.env.data,
                                                   camera="corner2", width=480, height=480)
        self.steps = 0
        self.phase_steps = {}
        self.phase = "prefix"
        self.limit = None
        self.original_step = self.env.step
        self.env.step = self._step
        self.events = []
        self.observations = []
        self.probe_count = 0
        self.grip = -1.
        self.latest = None
        self.finished = False
        self.blackout = {"normal": 0, "temporary": 1, "delayed": 2,
                         "persistent": None, "precontact": 1, "released": 1}[case]
        try:
            for _ in range(30):
                self.env.step(np.array([0., 0., 0., -1.]))
            rgb = self.env.render().copy()
            self.initial_blue = self._pixel(rgb, "blue")
            self.initial_green = self._pixel(rgb, "green")
            if self.initial_blue is None or self.initial_green is None:
                raise ValueError("missing_initial_fixture_target")
            request = self.contract.request(self._pick(), bindings=self._bindings("initial", self.initial_blue,
                                            self.initial_green), observation_id="initial")
            self.receipt = self.controller.execute(request, max_steps=20 if case == "precontact" else 260)
            active = [t for t in self.receipt.get("trace", []) if t.get("steps", 0)]
            self.precontact = bool(active and all(t["phase"] == 0 for t in active)
                                   and self.receipt.get("reason") == "waypoint_timeout")
            self.grip = -1. if self.precontact else 1.
            self.events.append({"action": "initial_pick", "receipt": self.receipt})
            self.release_commanded = case == "released"
            if self.release_commanded:
                for _ in range(30):
                    self.env.step(np.array([0., 0., 0., -1.]))
                self.grip = -1.
                self.events.append({"action": "open_and_settle", "steps": 30})
            # Public family is derived from observable commands and receipt.
            self.family = "released" if self.release_commanded else "precontact" if self.precontact else "post_pick"
            self.prefix_steps = self.steps
        except BaseException:
            self.close()
            raise

    def _step(self, action):
        if self.limit is not None and self.steps >= self.limit:
            raise ResourceLimit("explicit_control_step_cap")
        self.steps += 1
        self.phase_steps[self.phase] = self.phase_steps.get(self.phase, 0) + 1
        result = self.original_step(action)
        if result[2] or result[3]:
            raise RuntimeError("episode_ended")
        return result

    @staticmethod
    def _pick():
        from ch3.compiler.executable_plan import ExecutableStep
        return ExecutableStep(1, "fixed_pick", "grasp", {"object_id": "blue_candidate", "arm": "right"}, "pick")

    @staticmethod
    def _pixel(rgb, color):
        from ch3.execution.visual_holding import color_pixel
        try:
            return color_pixel(rgb, color)[0]
        except ValueError:
            return None

    def _bindings(self, obs_id, blue, green):
        from ch3.execution.visual_geometry import visual_binding
        return {"blue_candidate": visual_binding(self.calibration, object_id="blue_candidate",
            observation_id=obs_id, pixel=blue, plane_z=.02),
            "green_region": visual_binding(self.calibration, object_id="green_region",
            observation_id=obs_id, pixel=green, plane_z=.008)}

    def _probe(self):
        from PIL import Image
        from ch3.execution.visual_geometry import project_point
        from ch3.execution.visual_holding import holding_evidence
        from scripts.audit_recovery_observability import public_options
        np = self.np
        i = self.probe_count
        self.probe_count += 1
        origin = np.asarray(self.env.get_endeff_pos()).copy()
        samples = []
        clearance = True
        # External fault schedule depends on acquisition index, never on policy name.
        masked = self.blackout is None or i < self.blackout
        for frame_index, shift in enumerate((0., .04 if i % 2 == 0 else -.04)):
            waypoint = origin + [shift, 0., 0.]
            for _ in range(40):
                hand = self.env.get_endeff_pos()
                clearance &= bool(self.grip == -1 and hand[2] >= .12)
                self.env.step(np.r_[np.clip(10*(waypoint-hand), -1, 1), self.grip])
                if np.linalg.norm(self.env.get_endeff_pos()-waypoint) < .005:
                    break
            raw = self.env.render().copy()
            rgb = np.zeros_like(raw) if masked else raw
            Image.fromarray(np.flip(rgb, (0, 1))).save(self.output/f"feedback_{i}_{frame_index}.png")
            samples.append({"target_pixel": self._pixel(rgb, "blue"), "destination_pixel": self._pixel(rgb, "green"),
                            "hand_pixel": project_point(self.calibration, self.env.get_endeff_pos())})
        holding = holding_evidence(self.initial_blue, [s["target_pixel"] for s in samples],
                                   [s["hand_pixel"] for s in samples])
        obs_id = f"probe:{i}"
        options = public_options(holding=holding, target_visible=samples[-1]["destination_pixel"] is not None,
            initial_pixel=self.initial_blue, fresh_pixel=samples[-1]["target_pixel"], pick_result=self.receipt,
            precontact_probe_preserved=self.precontact and clearance, observations_left=3,
            observation_id=obs_id)
        label = "ready" if "continue" in options["allowed"] else "retry_ready" if "retry" in options["allowed"] else "unknown"
        self.latest = {"id": obs_id, "label": label, "holding": holding, "samples": samples, "options": options}
        self.observations.append(self.latest)
        return label

    def allowed(self):
        if self.finished:
            return frozenset({"stop"})
        allowed = {"stop", "observe"}
        if self.latest:
            allowed |= set(self.latest["options"]["allowed"]) & {"continue", "retry"}
        return frozenset(allowed)

    def _place(self):
        from ch3.execution.observed_continuation import checked_place_continuation
        np = self.np
        if "continue" not in self.allowed():
            raise ValueError("actual_fresh_holding_permission_required")
        obs = self.latest
        _, place = checked_place_continuation(obs["holding"], target_visible=True, observation_id=obs["id"])
        bindings = self._bindings(obs["id"], self.initial_blue, obs["samples"][-1]["destination_pixel"])
        bindings["blue_candidate"] = replace(bindings["blue_candidate"],
            position=tuple(float(v) for v in self.env.get_endeff_pos()))
        result = self.controller.execute(self.contract.request(place, bindings=bindings, observation_id=obs["id"]))
        self.events.append({"action": "place", "receipt": result})
        # Failed/partial execution has unknown gripper state: don't append a
        # guessed open-gripper settle sequence or a further command.
        if not result["completed"]:
            self.finished = True
            return "execution_finished"
        self.grip = -1.
        for _ in range(30):
            self.env.step(np.array([0., 0., 0., -1.]))
        self.finished = True
        return "execution_finished"

    def perform(self, action, cap):
        """Cap is in actual env.step units; always check live shared permissions."""
        if action not in self.allowed() or action == "stop":
            raise ValueError("inadmissible_action")
        before = self.steps
        self.phase = action
        self.limit = self.steps + cap
        clock = time.perf_counter()
        try:
            if action == "observe":
                label = self._probe()
            elif action == "continue":
                label = self._place()
            elif action == "retry":
                result = self.controller.retract_open()
                self.events.append({"action": "retract_open_not_episode_reset", "receipt": result})
                if not result["completed"]:
                    self.finished = True
                    return "execution_finished", self.steps-before
                rgb = self.env.render().copy()
                blue, green = self._pixel(rgb, "blue"), self._pixel(rgb, "green")
                from ch3.execution.early_pick_recovery import early_recovery_gate
                gate = early_recovery_gate(self.receipt, initial_empty_fixture=True,
                                           initial_pixel=self.initial_blue, fresh_pixel=blue)
                if not gate["authorized"] or green is None:
                    self.finished = True
                    return "execution_finished", self.steps-before
                request = self.contract.request(self._pick(), bindings=self._bindings("retry", blue, green),
                                                observation_id="retry")
                result = self.controller.execute(request)
                self.events.append({"action": "retry_pick", "receipt": result})
                self.grip, self.precontact = 1., False
                self._probe()
                label = self._place() if "continue" in self.allowed() else "execution_finished"
                self.finished = True
            else:
                raise ValueError("unsupported_macro")
            return label, self.steps-before
        finally:
            self.limit = None
            self.events.append({"action": action, "explicit_steps": self.steps-before,
                                "wall_seconds": time.perf_counter()-clock})

    def score(self):
        """Terminal-only independent geometry check; no policy callback follows."""
        np = self.np
        self.phase, self.limit = "terminal_scoring", None
        positions, good = [], []
        for _ in range(10):
            self.env.step(np.array([0., 0., 0., self.grip]))
            p = self.env.data.body("candidate_blue").xpos.copy()
            r = self.env.data.body("placement_region").xpos.copy()
            positions.append(p)
            good.append(bool(np.all(np.abs(p[:2]-r[:2]) <= [.052, .027])
                             and .01 <= p[2] <= .055 and self.grip == -1))
        return bool(all(good) and max(float(np.linalg.norm(b-a)) for a,b in zip(positions,positions[1:])) < .001)

    def close(self):
        self.env.close()
        self.tmp.cleanup()
