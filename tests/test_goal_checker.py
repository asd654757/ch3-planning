"""goal ⊆ S_T：满足 / 不满足 / pass-but-wrong 场景。"""
from ch3.goal import goal_satisfied
from ch3.schema.model_plan import Arm, GoalSpec, Skill
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from tests.conftest import act

SCENE = {"red_cube_0", "blue_cube_0", "tray_0", "box_0"}


def test_goal_satisfied_after_plan():
    s = WorldState.table_scene(SCENE)
    s, ok, _, _ = step(s, act(1, Skill.PICK, "red_cube_0", Arm.LEFT))
    assert ok
    s, ok, _, _ = step(s, act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"))
    assert ok
    goal = GoalSpec(facts=["on(red_cube_0, tray_0)", "on(blue_cube_0, table)"])
    assert goal_satisfied(s, goal, arms={"left", "right"})


def test_goal_not_satisfied_when_step_deleted():
    """pass-but-wrong 场景：合法但目标未完成。"""
    s = WorldState.table_scene(SCENE)
    # 只做了 A->tray，没做 B->box
    s, ok, _, _ = step(s, act(1, Skill.PICK, "red_cube_0", Arm.LEFT))
    s, ok, _, _ = step(s, act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0"))
    goal = GoalSpec(facts=["on(red_cube_0, tray_0)", "on(blue_cube_0, box_0)"])
    assert not goal_satisfied(s, goal, arms={"left", "right"})
