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
