"""Explicit mock session for testing the production orchestration entrypoint."""
from pathlib import Path

from ch3.capability.registry import load_registry
from ch3.state.world_state import WorldState
from ch3.validator.pipeline import Validator

from .core import Evidence, Observation, Supervisor, Task, Truth
from .runtime import Session


def build_session() -> Session:
    # Share the scripted planner/executor with the earlier smoke, not a real policy.
    from scripts.supervision_smoke import MockPlanner, MockPolicy

    registry = load_registry(Path(__file__).resolve().parents[2] / "config/capability_registry.yaml")
    objects = {"red_cube_0", "tray_0"}
    goal = "on(red_cube_0, tray_0)"
    supervisor = Supervisor(Task("Place the red cube on the tray", (goal,)),
                            Validator(objects, registry), MockPlanner())
    backend = MockPolicy(WorldState.table_scene(objects))

    class MockObserver:
        sequence = -1

        def read(self):
            self.sequence += 1
            state = backend.state.copy()
            truth = Truth.TRUE if goal in state.facts() else Truth.FALSE
            return Observation("mock", self.sequence, state,
                {arm: state.holding.get(arm) for arm in registry.arms},
                {goal: Evidence(truth, "mock_observer")})

    return Session(supervisor, MockObserver(), backend, "mock_integration_only")
