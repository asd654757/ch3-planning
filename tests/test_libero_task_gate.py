from types import SimpleNamespace

import numpy as np
import pytest

from ch3.supervision.libero_gate import LiberoTaskGate


TASK = "pick up the alphabet soup and place it in the basket"
ACTION_SPACE = SimpleNamespace(low=-np.ones(7), high=np.ones(7))


def observation():
    frames = {key: np.zeros((1, 4, 4, 3), dtype=np.uint8) for key in ("image", "image2")}
    return {"pixels": frames, "robot_state": {"eef": {"pos": np.zeros((1, 3)), "quat": np.ones((1, 4))},
                                              "gripper": {"qpos": np.zeros((1, 2))}}}


def test_missing_wrist_invalidates_ticket_and_bounded_reobservation():
    gate = LiberoTaskGate("libero_object", 0, TASK)
    assert gate.observe(observation()) == "supported"
    ticket = gate.context.issue_request()
    missing = observation()
    del missing["pixels"]["image2"]
    assert gate.observe(missing) == "unknown"
    assert gate.route(1) == "REOBSERVE"
    assert gate.route(2) == "STOP"
    with pytest.raises(ValueError, match="supported current observation"):
        gate.context.issue_request()
    assert gate.observe(observation()) == "supported"
    assert gate.route(1) == "CONTINUE"
    with pytest.raises(ValueError, match="stale"):
        gate.dispatch(ticket, np.zeros((1, 7)), ACTION_SPACE)
    new_ticket = gate.context.issue_request()
    assert np.array_equal(gate.dispatch(new_ticket, np.full((1, 7), 1.01), ACTION_SPACE), np.ones((1, 7)))
    with pytest.raises(ValueError, match="consumed"):
        gate.dispatch(new_ticket, np.ones((1, 7)), ACTION_SPACE)


def test_task_admission_and_invalid_action_do_not_establish_effects():
    with pytest.raises(ValueError, match="unregistered"):
        LiberoTaskGate("libero_object", 0, "place a different object")
    gate = LiberoTaskGate("libero_object", 0, TASK)
    observed = {**observation(), "is_success": True}
    assert gate.observe(observed) == "supported"
    ticket = gate.context.issue_request()
    with pytest.raises(ValueError, match="invalid LIBERO action"):
        gate.dispatch(ticket, np.full((1, 7), np.nan), ACTION_SPACE)
    assert len(gate.context.history) == 0 and not gate.context.current_facts
