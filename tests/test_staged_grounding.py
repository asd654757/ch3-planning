import pytest

from ch3.vlm.staged_grounding import (ImageBox, Localization, ObservationAnswer,
                                      merge_observation, missing_observation_requests,
                                      validate_observation_relation)


def test_declared_coordinate_conversion():
    box = ImageBox(x_min=200, y_min=300, x_max=400, y_max=500)
    assert box.normalized() == (0.2, 0.3, 0.4, 0.5)


@pytest.mark.parametrize("coords", [(0, 0, 1001, 1000), (10, 10, 0, 20), (-1, 0, 20, 20)])
def test_reject_invalid_boxes(coords):
    with pytest.raises(ValueError):
        ImageBox(**dict(zip(("x_min", "y_min", "x_max", "y_max"), coords)))


def test_reject_undeclared_coordinate_system():
    with pytest.raises(ValueError):
        Localization(coordinate_system="pixels", objects=[], uncertainty=[])


def test_evaluation_matches_are_one_to_one():
    from scripts.score_grounding_boxes import score
    box = {"x_min": 0, "y_min": 0, "x_max": 1000, "y_max": 1000}
    result = score([{"object_id": "a", "box": box}, {"object_id": "b", "box": box}],
                   {"truth": {"bbox": [0, 0, 100, 100]}}, 100, 100)
    assert result["matched_at_iou_0_5"] == 1


def test_localization_request_never_receives_instruction_or_truth():
    import json
    from ch3.vlm.client import VLMResponse
    from ch3.vlm.staged_grounding import StagedGrounder
    class Client:
        def complete(self, **kwargs):
            assert set(json.loads(kwargs["user_prompt"])) == {"output_schema"}
            return VLMResponse(content='{"coordinate_system":"image_0_1000","objects":[],"uncertainty":["occluded"]}', model="mock", latency_ms=0)
    result, _ = StagedGrounder(Client()).localize(image_path="frame.png", seed=0)
    assert result.uncertainty == ["occluded"]


def test_unknown_hand_remains_rejected_without_truth_fill():
    from ch3.vlm.scene_grounding import GroundedScene
    scene = GroundedScene.model_validate({
        "entities": [{"object_id": "red", "description": "red object", "confidence": 0.95}],
        "observed": [{"predicate": "on", "subject": "red", "target": "table", "confidence": 0.95}],
        "goal": [{"predicate": "on", "subject": "red", "target": "table", "confidence": 0.95}],
        "needs_observation": True, "uncertainty": ["hand occluded"],
    })
    with pytest.raises(ValueError, match="additional observation"):
        scene.to_planning_input(arms={"right"})


def test_missing_requests_are_derived_from_observed_facts():
    from ch3.vlm.scene_grounding import GroundedScene
    scene = GroundedScene.model_validate({
        "entities": [
            {"object_id": "red", "description": "red", "confidence": .95},
            {"object_id": "blue", "description": "blue", "confidence": .95},
        ], "observed": [{"predicate": "on", "subject": "red", "target": "table", "confidence": .95}],
        "goal": [{"predicate": "on", "subject": "red", "target": "blue", "confidence": .95}],
        "needs_observation": True, "uncertainty": ["missing"],
    })
    assert missing_observation_requests(scene, arms={"right"}) == ["location:blue", "hand_state:right"]


def test_program_missing_facts_are_independent_of_model_flag():
    from ch3.vlm.scene_grounding import GroundedScene
    scene = GroundedScene.model_validate({
        "entities": [{"object_id": "red", "description": "red", "confidence": .95}],
        "observed": [], "goal": [{"predicate": "on", "subject": "red", "target": "table", "confidence": .95}],
        "needs_observation": False, "uncertainty": [],
    })
    assert missing_observation_requests(scene, arms={"right"}) == ["location:red", "hand_state:right"]


def test_unresolved_observation_cannot_be_accepted():
    from ch3.vlm.scene_grounding import GroundedScene
    scene = GroundedScene.model_validate({
        "entities": [{"object_id": "red", "description": "red", "confidence": .95}],
        "observed": [], "goal": [{"predicate": "on", "subject": "red", "target": "table", "confidence": .95}],
        "needs_observation": True, "uncertainty": ["missing"],
    })
    answer = ObservationAnswer(observed=[], resolved=[], unresolved=["hand_state:right"], uncertainty=[])
    merged = merge_observation(scene, answer)
    assert merged.needs_observation
    assert merged.observed == []


def test_single_observation_requires_matching_relation():
    answer = ObservationAnswer(
        observed=[{"predicate": "hand_empty", "subject": "right", "target": None, "confidence": .95}],
        resolved=["hand_state:right"], unresolved=[], uncertainty=[])
    validate_observation_relation(answer, "hand_state:right")
    with pytest.raises(ValueError, match="requested relation"):
        validate_observation_relation(answer, "location:red")


def test_observation_answer_cannot_claim_both_states():
    with pytest.raises(ValueError, match="resolved and unresolved"):
        ObservationAnswer(observed=[], resolved=["hand_state:right"],
                          unresolved=["hand_state:right"], uncertainty=[])
