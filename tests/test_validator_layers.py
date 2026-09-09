"""四层校验：正例 + 各类错误码 + 首错定位。"""
from ch3.schema.model_plan import Arm, Skill
from ch3.validator import ErrorCode, Validator
from tests.conftest import act, plan


def make_validator(registry, scene_objects):
    return Validator(scene_objects=set(scene_objects), registry=registry)


def test_valid_plan(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),
        act(3, Skill.PICK, "blue_cube_0", Arm.RIGHT),
    )
    r = v.validate(p, init_state)
    assert r.valid and r.first_invalid_step is None
    assert [a.step_id for a in r.validated_prefix] == [1, 2, 3]
    assert r.final_state.holding.get("right") == "blue_cube_0"


def test_step_id_not_continuous(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(act(2, Skill.PICK, "red_cube_0", Arm.LEFT))  # 从 2 开始
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.SCHEMA_ERROR and r.layer == "syntax"
    assert r.first_invalid_step == 2


def test_unknown_object(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(act(1, Skill.PICK, "ghost_0", Arm.LEFT))
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.UNKNOWN_OBJECT and r.layer == "object"
    assert r.first_invalid_step == 1


def test_unknown_target(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "ghost_tray"),
    )
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.UNKNOWN_OBJECT and r.layer == "object"
    assert r.first_invalid_step == 2


def test_place_missing_target(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, None),  # place 缺 target
    )
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.MISSING_PARAMETER and r.layer == "capability"
    assert r.first_invalid_step == 2


def test_pick_with_target_rejected(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(act(1, Skill.PICK, "red_cube_0", Arm.LEFT, "tray_0"))
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.MISSING_PARAMETER and r.layer == "capability"


def test_unregistered_skill(init_state):
    from ch3.capability.registry import CapabilityRegistry
    # 注册表只含 pick，不含 place
    reg = CapabilityRegistry({"arms": ["left", "right"], "capabilities": {"pick": {}}, "special_targets": {"table": {}}})
    v = Validator(scene_objects={"red_cube_0", "tray_0"}, registry=reg)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),
    )
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.UNREGISTERED_SKILL and r.layer == "capability"
    assert r.first_invalid_step == 2


def test_arm_not_empty(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PICK, "blue_cube_0", Arm.LEFT),  # left 已 holding red
    )
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.ARM_NOT_EMPTY and r.layer == "state"
    assert r.first_invalid_step == 2
    assert [a.step_id for a in r.validated_prefix] == [1]


def test_place_before_pick(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(act(1, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"))  # 未持有就 place
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.OBJECT_NOT_HELD and r.layer == "state"
    assert r.first_invalid_step == 1


def test_target_held_by_other_arm(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PICK, "tray_0", Arm.RIGHT),      # tray 被右手持
        act(3, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),  # 目标被手持 -> E07
    )
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.TARGET_NOT_FOUND and r.layer == "state"
    assert r.first_invalid_step == 3


def test_pick_object_not_on_table(registry, scene_objects, init_state):
    v = make_validator(registry, scene_objects)
    p = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),
        act(3, Skill.PICK, "red_cube_0", Arm.LEFT),  # red 已在 tray 上，不在 table
    )
    r = v.validate(p, init_state)
    assert not r.valid and r.error_code == ErrorCode.STATE_TRANSITION_ERROR and r.layer == "state"
    assert r.first_invalid_step == 3
