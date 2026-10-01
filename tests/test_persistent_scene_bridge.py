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
