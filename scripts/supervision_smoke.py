"""Mock-only integration demo; no weights, API calls, simulator or robot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ch3.capability.registry import load_registry
from ch3.schema.model_plan import ModelPlanAction
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from ch3.supervision import Evidence, Observation, Receipt, Supervisor, Task, Truth
from ch3.validator.pipeline import Validator


class MockPlanner:
    def generate(self, request, images):
        # Scripted test double: proves wiring, not language/model performance.
        return json.dumps({"actions": [
            {"step_id": 1, "skill": "pick", "object_id": "red_cube_0", "arm": "left"},
            {"step_id": 2, "skill": "place", "object_id": "red_cube_0", "arm": "left", "target_id": "tray_0"},
        ]})


class MockPolicy:
    def __init__(self, state):
        self.state = state.copy()
        self.count = 0

    def execute(self, executable, **kwargs):
        self.count += 1
        if self.count == 1:
            return Receipt("failed", "scripted grasp miss")
        action = ModelPlanAction(step_id=1, skill=executable.source_skill, **executable.args)
        self.state, ok, _, message = step(self.state, action)
        return Receipt("success" if ok else "failed", message)


def run():
    root = Path(__file__).resolve().parents[1]
    registry = load_registry(root / "config/capability_registry.yaml")
    objects = {"red_cube_0", "tray_0"}
    goal = "on(red_cube_0, tray_0)"
    supervisor = Supervisor(Task("Place the red cube on the tray", (goal,)),
        Validator(objects, registry), MockPlanner())
    policy = MockPolicy(WorldState.table_scene(objects))
    events = []
    for sequence in range(10):
        # Mock observer sees test-double state, independent of receipt status.
        truth = Truth.TRUE if goal in policy.state.facts() else Truth.FALSE
        supervisor.observe(Observation("mock", sequence, policy.state,
            {a: policy.state.holding.get(a) for a in registry.arms},
            {goal: Evidence(truth, "mock_observer")}))
        status = supervisor.prepare()
        if status != "ready":
            break
        receipt = supervisor.execute_next(policy)
        events.append({"action": supervisor.history[-1]["action"], "status": receipt.status})
    result = {"evidence_kind": "mock_integration_only", "status": supervisor.status,
        "model_calls": supervisor.model_calls, "commands": len(supervisor.history),
        "events": events, "audit": supervisor.audit}
    assert result["status"] == "complete"
    assert [e["action"]["skill"] for e in events] == ["pick", "pick", "place"]
    assert result["model_calls"] == 2
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "audit"}, ensure_ascii=False))
