"""第四层：离散状态与前置条件校验（用状态模拟器逐动作推进）。"""

from __future__ import annotations

from typing import Optional

from ch3.schema.model_plan import ModelPlan
from ch3.state.simulator import step
from ch3.state.world_state import WorldState
from ch3.errors import ErrorCode


def simulate_plan(
    plan: ModelPlan,
    state: WorldState,
    valid_targets: Optional[set[str]] = None,
    pick_surfaces: Optional[set[str]] = None,
) -> tuple[bool, Optional[int], Optional[ErrorCode], str, WorldState]:
    """从初始状态逐动作推进；返回 (全部通过?, 首错 step_id, 错误码, 消息, 终点状态)。

    全部通过时 first_invalid_step=None、error_code=None。
    失败时返回的错误码属于 state 层（E05-E08）。
    """
    cur = state.copy()
    for action in plan.actions:
        nxt, ok, code, msg = step(cur, action, valid_targets=valid_targets, pick_surfaces=pick_surfaces)
        if not ok:
            return False, action.step_id, code, msg, cur
        cur = nxt
    return True, None, None, "", cur
