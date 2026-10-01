from dataclasses import replace

import pytest

from ch3.compiler.executable_plan import ExecutableStep
from ch3.execution.skill_contract import BackendContract, TargetBinding


def fixture():
    contract = BackendContract("fixed_skill_controller", frozenset({"pick", "place"}),
                               frozenset({"right"}), "robot_base")
    step = ExecutableStep(1, "adflow_grasp", "grasp",
                          {"object_id": "red", "arm": "right"}, "pick")
    binding = TargetBinding("red", (.1, .2, .03), "robot_base", "frame_1", "visual_estimate")
    return contract, step, binding


def test_legacy_policy_name_does_not_identify_actual_backend():
    contract, step, binding = fixture()
    request = contract.request(step, bindings={"red": binding}, observation_id="frame_1")
    assert request.skill == "pick"
    assert request.backend == "fixed_skill_controller"


@pytest.mark.parametrize("changes,reason", [
    ({"source": "simulator_truth"}, "binding source"),
    ({"observation_id": "old"}, "stale"),
    ({"coordinate_frame": "image_pixels"}, "coordinate frame"),
    ({"object_id": "blue"}, "mismatched"),
    ({"position": (float("nan"), 0, 0)}, "position"),
])
def test_unsafe_bindings_are_rejected(changes, reason):
    contract, step, binding = fixture()
    with pytest.raises(ValueError, match=reason):
        contract.request(step, bindings={"red": replace(binding, **changes)}, observation_id="frame_1")


def test_no_default_object_alias():
    contract, step, _ = fixture()
    with pytest.raises(ValueError, match="missing"):
        contract.request(step, bindings={}, observation_id="frame_1")


def test_place_requires_destination():
    contract, step, binding = fixture()
    with pytest.raises(ValueError, match="destination"):
        contract.request(replace(step, source_skill="place"),
                         bindings={"red": binding}, observation_id="frame_1")
