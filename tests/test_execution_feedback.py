"""Execution Feedback：低层失败不得被当作成功（阶段1 注入回归用例）。"""
from ch3.schema.model_plan import Arm, Skill
from ch3.state.feedback import ExecutionFeedback, SimulatedFeedbackProvider
from ch3.state.world_state import WorldState
from tests.conftest import act

SCENE = {"red_cube_0", "blue_cube_0", "tray_0", "box_0"}


def test_pick_failure_returns_failed():
    """pick 一个不在 table 上的对象 -> FAILED，绝不当作 SUCCESS 推进。"""
    provider = SimulatedFeedbackProvider()
    s = WorldState.table_scene(SCENE)
    # 先让 red 不在 table：放到 tray 上
    s, ok, _, _ = __import__("ch3.state.simulator", fromlist=["step"]).step(
        s, act(1, Skill.PICK, "red_cube_0", Arm.LEFT)
    )
    s, ok, _, _ = __import__("ch3.state.simulator", fromlist=["step"]).step(
        s, act(2, Skill.PLACE, "red_cube_0", Arm.LEFT, "tray_0")
    )
    # 再尝试 pick red（在 tray 上，不在 table）-> 执行反馈 FAILED
    fb = provider.execute(s, act(3, Skill.PICK, "red_cube_0", Arm.LEFT))
    assert fb == ExecutionFeedback.FAILED
    # 状态未被推进（holding 仍为空）
    assert s.arm_empty("left")
