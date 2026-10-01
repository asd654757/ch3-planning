from dataclasses import replace

import numpy as np
import pytest

from ch3.execution.fixed_pickplace import FixedPickPlaceController
from ch3.execution.skill_contract import SkillRequest, TargetBinding
from scripts.multiobject_pickplace_smoke import color_pixel


class MockRobot:
    def __init__(self):
        self.position = np.array([0., .6, .2])
        self.actions = []

    def get_endeff_pos(self):
        return self.position.copy()

    def step(self, action):
        self.actions.append(action.copy())
        self.position += action[:3] * .02
        return None, 0., False, False, {}


def request():
    binding = TargetBinding("blue", (0., .6, .02), "mujoco_world", "frame1", "visual_estimate")
    return SkillRequest(1, "pick", "right", binding, None,
                        FixedPickPlaceController.backend, "frame1")


def test_waypoint_completion_is_not_grasp_success():
    robot = MockRobot()
    result = FixedPickPlaceController(robot).execute(request())
    assert result["completed"]
    assert result["reason"] == "waypoints_completed_not_grasp_verified"
    assert result["trace"][2]["steps"] == 35
    assert "success" not in result
    assert len(robot.actions) == result["steps"]


def test_exact_budget_completion_and_short_budget_timeout():
    steps = FixedPickPlaceController(MockRobot()).execute(request())["steps"]
    assert FixedPickPlaceController(MockRobot()).execute(request(), max_steps=steps)["completed"]
    result = FixedPickPlaceController(MockRobot()).execute(request(), max_steps=steps - 1)
    assert not result["completed"]
    assert result["steps"] == steps - 1


@pytest.mark.parametrize("binding", [
    replace(request().object_binding, source="simulator_truth"),
    replace(request().object_binding, observation_id="old"),
    replace(request().object_binding, position=(float("nan"), .6, .02)),
    replace(request().object_binding, position=(0., .6)),
    replace(request().object_binding, position=(0., .1, .02)),
])
def test_rejects_invalid_binding_before_motion(binding):
    robot = MockRobot()
    with pytest.raises(ValueError):
        FixedPickPlaceController(robot).execute(replace(request(), object_binding=binding))
    assert not robot.actions


def test_rgb_only_color_heuristic():
    frame = np.zeros((30, 30, 3), dtype=np.uint8)
    frame[10:15, 4:9] = [0, 0, 200]
    frame[20:25, 20:25] = [0, 200, 0]
    assert color_pixel(frame, "blue") == ((6., 12.), 25)
    assert color_pixel(frame, "green") == ((22., 22.), 25)
    with pytest.raises(ValueError):
        color_pixel(np.zeros_like(frame), "blue")
