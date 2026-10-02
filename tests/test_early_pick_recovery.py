import pytest
from ch3.execution.early_pick_recovery import early_recovery_gate
from ch3.execution.fixed_pickplace import FixedPickPlaceController
from tests.test_fixed_pickplace import MockRobot


def result(phase=0):
    return {"reason": "waypoint_timeout", "trace": [{"phase": phase, "steps": 8}]}


def gate(value, **kwargs):
    return early_recovery_gate(value, initial_empty_fixture=kwargs.get("fixture", True),
                               initial_pixel=(100, 100), fresh_pixel=kwargs.get("pixel", (100, 100)))


def test_precontact_permission_is_explicitly_not_visual_empty_hand():
    decision = gate(result())
    assert decision["authorized"]
    assert not decision["empty_hand_visually_estimated"]


@pytest.mark.parametrize("value,kwargs", [(result(1), {}), (result(2), {}),
    (result(), {"fixture": False}), (result(), {"pixel": None}),
    (result(), {"pixel": (110, 100)}), (result(), {"pixel": (float("nan"), 100)}),
    ({"reason": "waypoints_completed_not_grasp_verified", "trace": []}, {})])
def test_refuses_contact_closure_missing_or_changed_evidence(value, kwargs):
    assert not gate(value, **kwargs)["authorized"]


def test_retraction_is_bounded_and_only_commands_open_gripper():
    robot = MockRobot()
    outcome = FixedPickPlaceController(robot).retract_open()
    assert outcome["completed"] and outcome["steps"] <= 80
    assert all(action[3] == -1 for action in robot.actions)
    assert not FixedPickPlaceController(MockRobot()).retract_open(max_steps=1)["completed"]
