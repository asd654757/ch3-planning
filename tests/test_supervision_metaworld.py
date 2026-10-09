"""Observation/backend boundary tests; no simulator dependencies required."""
import numpy as np
from types import SimpleNamespace
import pytest
from ch3.supervision.metaworld_adapter import decode_state, MetaWorldBackend, GOAL, OBJECT


def read(puck, hand, gripper):
    return decode_state({'puck_pos': np.array(puck), 'hand_pos': np.array(hand),
        'target_pos': np.array([0., 0., .02]), 'gripper_distance': gripper},
        goal_tolerance=.08, closed_threshold=.73, lift_threshold=.04)


def test_closed_gripper_at_target_is_not_completed_goal():
    state = read([0,0,.02], [0,0,.03], .1)
    assert GOAL not in state.facts()
    assert state.holding == {'right': OBJECT}
    assert OBJECT not in state.at


def test_released_object_away_from_goal_is_on_table():
    state = read([.2,0,.02], [.2,0,.1], 1.)
    assert state.at[OBJECT] == 'table'
    assert not state.holding


def test_released_object_at_target_has_goal_fact():
    assert GOAL in read([0,0,.02], [0,0,.1], 1.).facts()


def test_unsupported_target_never_reaches_controller():
    class NeverExecute:
        def execute_step(self, *args, **kwargs):
            pytest.fail('unsafe command dispatched')
    backend = MetaWorldBackend(NeverExecute())
    step = SimpleNamespace(source_skill='place', args={'object_id': OBJECT,
        'target_id': 'table', 'arm': 'right'})
    with pytest.raises(ValueError, match='unmapped'):
        backend.execute(step, instruction='', context={}, command_id='test')
