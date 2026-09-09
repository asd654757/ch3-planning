from __future__ import annotations

from ch3.repair.prefix_guard import merge_locked_prefix
from ch3.schema.model_plan import ModelPlan, ModelPlanAction


def action(step_id: int, skill: str, object_id: str) -> ModelPlanAction:
    return ModelPlanAction(
        step_id=step_id,
        skill=skill,
        object_id=object_id,
        target_id=None,
        arm="right",
    )


def test_rejects_returned_prefix_overlap() -> None:
    prefix = [action(1, "pick", "red_cube_0")]
    returned = ModelPlan(
        actions=[
            action(1, "place", "red_cube_0"),
            action(2, "pick", "blue_cube_0"),
        ]
    )
    result = merge_locked_prefix(prefix, returned)
    assert result.accepted is False
    assert result.reject_reason == "prefix_overlap"
    assert result.merged_plan is None


def test_rejects_wrong_suffix_start() -> None:
    prefix = [action(1, "pick", "red_cube_0")]
    returned = ModelPlan(actions=[action(3, "place", "red_cube_0")])
    result = merge_locked_prefix(prefix, returned)
    assert result.accepted is False
    assert result.reject_reason == "suffix_start_mismatch"


def test_rejects_nonconsecutive_suffix() -> None:
    prefix = [action(1, "pick", "red_cube_0")]
    returned = ModelPlan(
        actions=[
            action(2, "place", "red_cube_0"),
            action(4, "pick", "blue_cube_0"),
        ]
    )
    result = merge_locked_prefix(prefix, returned)
    assert result.accepted is False
    assert result.reject_reason == "suffix_step_ids_not_consecutive"


def test_merges_valid_suffix_without_mutating_inputs() -> None:
    prefix = [action(1, "pick", "red_cube_0")]
    returned = ModelPlan(actions=[action(2, "place", "red_cube_0")])
    result = merge_locked_prefix(prefix, returned)
    assert result.accepted is True
    assert result.reject_reason is None
    assert result.merged_plan is not None
    assert [a.step_id for a in result.merged_plan.actions] == [1, 2]
    assert result.merged_plan.actions[0] is not prefix[0]
    assert result.merged_plan.actions[1] is not returned.actions[0]


def test_rejects_noncontiguous_validated_prefix() -> None:
    prefix = [action(1, "pick", "red_cube_0"), action(3, "place", "red_cube_0")]
    returned = ModelPlan(actions=[action(4, "pick", "blue_cube_0")])
    result = merge_locked_prefix(prefix, returned)
    assert result.accepted is False
    assert result.reject_reason == "validated_prefix_not_contiguous_from_one"


def test_no_prefix_requires_contiguous_full_replacement() -> None:
    returned = ModelPlan(actions=[action(2, "pick", "red_cube_0")])
    result = merge_locked_prefix([], returned)
    assert result.accepted is False
    assert result.reject_reason == "replacement_step_ids_not_contiguous_from_one"
