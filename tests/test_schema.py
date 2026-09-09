"""schema：合法/非法构造与 pydantic 约束。"""
import pytest
from pydantic import ValidationError

from ch3.schema.model_plan import Arm, ModelPlan, ModelPlanAction, Skill
from tests.conftest import act, plan


def test_valid_action():
    a = act(1, Skill.PICK, "red_cube_0", Arm.LEFT)
    assert a.step_id == 1 and a.skill == Skill.PICK and a.target_id is None


def test_missing_object_id_rejected():
    with pytest.raises(ValidationError):
        ModelPlanAction(step_id=1, skill=Skill.PICK, arm=Arm.LEFT)  # 缺 object_id


def test_bad_skill_rejected():
    with pytest.raises(ValidationError):
        ModelPlanAction(step_id=1, skill="fly", object_id="red_cube_0", arm=Arm.LEFT)


def test_empty_plan_rejected():
    with pytest.raises(ValidationError):
        ModelPlan(actions=[])


def test_plan_json_roundtrip():
    p = plan(act(1, Skill.PICK, "red_cube_0", Arm.LEFT), act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"))
    data = p.model_dump()
    again = ModelPlan.model_validate(data)
    assert again == p
