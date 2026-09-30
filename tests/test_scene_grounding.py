import json

import pytest

from ch3.vlm.scene_grounding import GroundedScene, SceneGrounder
from ch3.vlm.client import VLMResponse


def scene():
    return {
        "entities": [{"object_id": "block", "description": "red block", "confidence": 0.95}],
        "observed": [
            {"predicate": "on", "subject": "block", "target": "table", "confidence": 0.95},
            {"predicate": "hand_empty", "subject": "right", "confidence": 0.95},
        ],
        "goal": [{"predicate": "holding", "subject": "right", "target": "block", "confidence": 0.95}],
        "needs_observation": False, "uncertainty": [],
    }


def test_conversion():
    state, goal = GroundedScene.model_validate(scene()).to_planning_input(arms={"right"})
    assert state.at == {"block": "table"}
    assert goal.facts == ["holding(right, block)"]


@pytest.mark.parametrize("failure", ["missing_hand", "low_confidence", "unknown", "conflict", "uncertainty"])
def test_reject_unsafe_observations(failure):
    data = scene()
    if failure == "missing_hand":
        data["observed"].pop()
    elif failure == "low_confidence":
        data["observed"][0]["confidence"] = 0.2
    elif failure == "unknown":
        data["goal"][0]["target"] = "unseen"
    elif failure == "conflict":
        data["observed"].append(data["goal"][0])
    else:
        data["uncertainty"] = ["occluded gripper"]
    with pytest.raises(ValueError):
        GroundedScene.model_validate(data).to_planning_input(arms={"right"})


def test_grounder_only_sends_allowlisted_inputs():
    class Client:
        def complete(self, **kwargs):
            prompt = json.loads(kwargs["user_prompt"])
            assert set(prompt) == {"instruction", "available_skills", "available_arms", "output_schema"}
            assert kwargs["image_path"] == "frame.png"
            return VLMResponse(content=json.dumps(scene()), model="mock", latency_ms=0)

    result, _ = SceneGrounder(Client()).ground(
        instruction="Pick the red block", image_path="frame.png",
        skills=["pick", "place"], arms=["right"], seed=0,
    )
    assert result.entities[0].object_id == "block"


@pytest.mark.parametrize("bbox", [None, [0, 0, 0, 1], [-0.1, 0, 1, 1], [0, 0, 1.1, 1]])
def test_visual_bindings_reject_invalid_regions(bbox):
    data = scene()
    data["entities"][0]["bbox"] = bbox
    with pytest.raises(ValueError):
        GroundedScene.model_validate(data).visual_bindings()


def test_visual_bindings_are_image_regions():
    data = scene()
    data["entities"][0]["bbox"] = [0.2, 0.3, 0.4, 0.5]
    assert GroundedScene.model_validate(data).visual_bindings() == {"block": (0.2, 0.3, 0.4, 0.5)}


def test_manifest_rejects_hidden_truth(tmp_path):
    from scripts.unstructured_grounding_pilot import load_manifest
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"case_id": "one", "instruction": "Pick red", "image_path": "frame.png", "seed": 0, "true_state": {}}]))
    with pytest.raises(ValueError, match="public fields"):
        load_manifest(manifest)


def test_manifest_resolves_relative_images(tmp_path):
    from scripts.unstructured_grounding_pilot import load_manifest
    (tmp_path / "frame.png").write_bytes(b"fixture")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"case_id": "one", "instruction": "Pick red", "image_path": "frame.png", "seed": 0}]))
    assert load_manifest(manifest)[0]["image_path"] == str(tmp_path / "frame.png")
