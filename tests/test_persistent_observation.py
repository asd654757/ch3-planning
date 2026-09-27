"""Regression tests for the single-object state-feedback pilot boundary."""

import numpy as np

from scripts.sim_persistent_recovery import observe


class FakeExecutor:
    goal_tolerance = 0.08
    gripper_closed_threshold = 0.73
    _held_symbolic_object = None

    def __init__(self, puck, hand, gripper):
        self.raw = {
            "puck_pos": np.array(puck), "hand_pos": np.array(hand),
            "target_pos": np.array([0.0, 0.8, 0.02]),
            "gripper_distance": gripper,
        }

    def _state(self):
        return self.raw

    def _is_holding(self, raw):
        return raw["gripper_distance"] <= 0.73 and raw["puck_pos"][2] >= 0.04


def test_closed_gripper_near_goal_is_not_finished():
    executor = FakeExecutor([0.0, 0.8, 0.02], [0.0, 0.8, 0.06], 0.5)
    state, _ = observe(executor)
    assert state.holding == {"right": "red_cube_0"}
    assert state.at["red_cube_0"] == "table"


def test_open_gripper_near_goal_is_observed_placed():
    executor = FakeExecutor([0.0, 0.8, 0.02], [0.0, 0.8, 0.06], 1.0)
    state, _ = observe(executor)
    assert state.holding == {}
    assert state.at["red_cube_0"] == "tray_1"
