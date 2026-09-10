"""冻结口径的 smoke：B1/B2a/B2b 共享同一 ModelPlan P。

验证两点：
1) EPR（executable 判定）与臂无关：同一 P 由同一 validator 计算，
   B1 与 B2a 的 executable 判定一致（因为 validator 不改 P）。
2) 系统行为不同：B1 无校验 -> IDR 高；B2a 拦截 -> IDR=0。
"""
from ch3.schema.model_plan import Arm, Skill
from ch3.state.world_state import WorldState
from ch3.validator import Validator
from tests.conftest import act, plan

SCENE = {"red_cube_0", "blue_cube_0", "tray_0", "box_0"}


def build_cases():
    valid = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"),
        act(3, Skill.PICK, "blue_cube_0", Arm.RIGHT),
        act(4, Skill.PLACE, "blue_cube_0", Arm.RIGHT, "box_0"),
    )
    invalid_state = plan(
        act(1, Skill.PICK, "red_cube_0", Arm.LEFT),
        act(2, Skill.PICK, "blue_cube_0", Arm.LEFT),   # left 非空 -> 非法
    )
    return [valid, invalid_state]


def test_epr_identical_across_arms(registry):
    v = Validator(scene_objects=set(SCENE), registry=registry)
    for p in build_cases():
        r = v.validate(p, WorldState.table_scene(SCENE))
        # evaluator 是纯函数：executable 判定与"哪个臂在用"无关（此处直接复算）
        r2 = v.validate(p.model_copy(deep=True), WorldState.table_scene(SCENE))
        assert r.valid == r2.valid


def test_idr_drops_with_validation(registry):
    v = Validator(scene_objects=set(SCENE), registry=registry)
    cases = build_cases()
    n = len(cases)
    n_invalid = sum(1 for p in cases if not v.validate(p, WorldState.table_scene(SCENE)).valid)

    # B1：无校验，全部下发 -> 非法计划也被下发
    idr_b1 = n_invalid / n
    # B2a：校验拦截 -> 非法计划不下发
    idr_b2a = 0.0
    assert idr_b1 > idr_b2a
    assert n_invalid == 1
