import pytest

from ch3.supervision.libero_semantic import LiberoSemanticSupervisor, ManipulationSample


FLOOR = ManipulationSample(0.0, False, False, False, False, 0.0)
HELD = ManipulationSample(0.03, True, True, False, False, 0.15)
FALLING_IN_BASKET = ManipulationSample(0.06, False, False, True, False, 1.0)
SETTLED = ManipulationSample(0.025, False, False, True, True, 0.015)


def test_phases_are_authorized_only_by_observed_pick_and_settled_place():
    supervisor = LiberoSemanticSupervisor(FLOOR, lambda _: None)
    assert supervisor.authorize() == "pick"
    assert supervisor.runtime.snapshot.history == ()
    with pytest.raises(ValueError, match="contact and lift"):
        supervisor.confirm_pick(FLOOR, "no-grasp")
    supervisor.await_pick_evidence("lost-wrist")
    with pytest.raises(ValueError, match="awaiting evidence"):
        supervisor.authorize()
    supervisor.confirm_pick(HELD, "new-grasp-evidence")
    assert supervisor.runtime.original_history[0].evidence == "unknown"
    assert supervisor.runtime.snapshot.history[0].evidence == "confirmed"
    assert supervisor.authorize() == "place"
    with pytest.raises(ValueError, match="settled basket"):
        supervisor.confirm_place(FALLING_IN_BASKET, "arrived-still-falling")
    supervisor.confirm_place(SETTLED, "settled")
    assert supervisor.authorize() is None
    assert [event.kind for event in supervisor.runtime.snapshot.history] == ["pick", "place"]
    assert supervisor.runtime.snapshot.contract.version == 1


def test_supported_drop_replans_for_the_same_goal_and_preserves_events():
    supervisor = LiberoSemanticSupervisor(FLOOR, lambda _: None)
    assert supervisor.authorize() == "pick"
    supervisor.confirm_pick(HELD, "held")
    assert supervisor.authorize() == "place"
    supervisor.observe_drop(FLOOR, "drop")
    assert supervisor.authorize() == "pick"
    assert supervisor.runtime.snapshot.contract.version == 1
    assert [(event.kind, event.target) for event in supervisor.runtime.snapshot.history] == [
        ("pick", None), ("place", "table"),
    ]
