import json
import pytest
from ch3.vlm.client import VLMResponse
from ch3.vlm.persistent_scene_bridge import GoalSelection, select_goal, generate_remaining


class Client:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return VLMResponse(json.dumps(self.content), "mock", 0)


def paths(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"test")
    return dict(image_path=image, log_path=tmp_path / "call.json")


def goal():
    return GoalSelection(object_id="blue_candidate", target_id="green_region", needs_observation=False, unsupported_constraints=[])


def test_goal_rejects_other_objects_and_keeps_raw(tmp_path):
    client = Client(dict(object_id="yellow", target_id="green_region", needs_observation=False, unsupported_constraints=[]))
    with pytest.raises(ValueError):
        select_goal(client, instruction="move yellow", **paths(tmp_path))
    assert (tmp_path / "call.json").exists()


def test_remainder_validated_from_actual_holding(tmp_path):
    client = Client({"actions": [dict(step_id=1, skill="place", arm="right", object_id="blue_candidate", target_id="green_region")]})
    step = generate_remaining(client, evidence={"status": "holding_supported"}, goal=goal(), **paths(tmp_path))
    assert step.step_id == 2 and step.policy_id == "fixed_place"


def test_unknown_does_not_call_model(tmp_path):
    client = Client({})
    with pytest.raises(ValueError):
        generate_remaining(client, evidence={"status": "unknown"}, goal=goal(), **paths(tmp_path))
    assert client.calls == 0


def test_duplicate_pick_rejected_not_replaced_with_fixed_plan(tmp_path):
    client = Client({"actions": [dict(step_id=1, skill="pick", arm="right", object_id="blue_candidate")]})
    with pytest.raises(ValueError):
        generate_remaining(client, evidence={"status": "holding_supported"}, goal=goal(), **paths(tmp_path))
    assert (tmp_path / "call.json").exists()


def test_constraints_not_silently_ignored(tmp_path):
    client = Client(dict(object_id="blue_candidate", target_id="green_region", needs_observation=False, unsupported_constraints=["do not move yellow"]))
    with pytest.raises(ValueError):
        select_goal(client, instruction="move blue but never move yellow", **paths(tmp_path))


def test_injected_fault_is_rejected_without_fallback(tmp_path):
    client = Client({"actions": [dict(step_id=1, skill="place", arm="right", object_id="blue_candidate", target_id="green_region")]})
    with pytest.raises(ValueError):
        generate_remaining(client, evidence={"status": "holding_supported"}, goal=goal(),
                           inject_unknown_target=True, **paths(tmp_path))
    audit = json.loads((tmp_path / "remaining_plan_validation.json").read_text())
    assert not audit["initial_valid"] and not audit["repair_attempted"]
    assert audit["original_model_plan"]["actions"][0]["target_id"] == "green_region"
    assert client.calls == 1


def test_current_state_fallback_never_replays_pick(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ch3.schema.model_plan import ModelPlan
    from ch3.vlm.repair import PlanRepairer
    valid = {"actions": [dict(step_id=1, skill="place", arm="right", object_id="blue_candidate", target_id="green_region")]}
    def fake_repair(self, **kwargs):
        assert kwargs["repair_mode"] == "R1_FROM_STATE"
        assert kwargs["validation"].validated_prefix == []
        assert kwargs["validation"].final_state.holding == {"right": "blue_candidate"}
        return SimpleNamespace(accepted=True, plan=ModelPlan.model_validate(valid),
            response=VLMResponse(json.dumps(valid), "mock", 0), prompt="test", reject_reason=None)
    monkeypatch.setattr(PlanRepairer, "repair", fake_repair)
    step = generate_remaining(Client(valid), evidence={"status": "holding_supported"}, goal=goal(),
        inject_unknown_target=True, repair_on_rejection=True, **paths(tmp_path))
    assert step.source_skill == "place" and step.step_id == 2
    audit = json.loads((tmp_path / "remaining_plan_validation.json").read_text())
    assert audit["repair_attempted"] and audit["repaired_valid"]
    assert not audit["executed_prefix_replayed"]
