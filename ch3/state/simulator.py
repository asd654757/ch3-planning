"""状态推进单一接口：step(state, action) -> (next_state, ok, error_code, message)。

离线 = 状态模拟器（本文件）；真机 = 同一签名，由 Execution Feedback 更新。
失败语义（错误码与冻结文档一致）：
- pick  前置失败：手非空 -> ARM_NOT_EMPTY；对象不在 table -> STATE_TRANSITION_ERROR
- place 前置失败：未持有该对象 -> OBJECT_NOT_HELD；目标不可用 -> TARGET_NOT_FOUND
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from ch3.schema.model_plan import ModelPlanAction, Skill
from ch3.state.world_state import WorldState
from ch3.errors import ErrorCode


def step(
    state: WorldState,
    action: ModelPlanAction,
    valid_targets: Optional[set[str]] = None,
    pick_surfaces: Optional[set[str]] = None,
) -> tuple[WorldState, bool, Optional[ErrorCode], str]:
    """推进一个动作。失败时返回原状态（拷贝）与错误码。"""
    targets: set[str] = valid_targets if valid_targets is not None else state.objects | {state.table_id}
    arm = action.arm.value
    obj = action.object_id

    if action.skill == Skill.PICK:
        if not state.arm_empty(arm):
            return state.copy(), False, ErrorCode.ARM_NOT_EMPTY, f"{arm} 非空，不能 pick {obj}"
        supported = {state.table_id} if pick_surfaces is None else {state.table_id} | set(pick_surfaces)
        if state.location_of(obj) not in supported:
            return state.copy(), False, ErrorCode.STATE_TRANSITION_ERROR, f"{obj} 不在 table 上，无法 pick"
        nxt = state.copy()
        nxt.holding[arm] = obj
        return nxt, True, None, ""

    if action.skill == Skill.PUSH:
        if not state.arm_empty(arm):
            return state.copy(), False, ErrorCode.ARM_NOT_EMPTY, f"{arm} 非空，不能 push {obj}"
        if not state.is_on_table(obj):
            return state.copy(), False, ErrorCode.STATE_TRANSITION_ERROR, f"{obj} 不在 table 上，无法 push"
        tgt = action.target_id
        if tgt is None or tgt not in targets:
            return state.copy(), False, ErrorCode.TARGET_NOT_FOUND, f"push 目标 {tgt} 不可用"
        nxt = state.copy()
        nxt.at[obj] = tgt
        nxt.pushed.add(obj)
        return nxt, True, None, ""

    if action.skill == Skill.PRESS:
        if not state.arm_empty(arm):
            return state.copy(), False, ErrorCode.ARM_NOT_EMPTY, f"{arm} 非空，不能 press {obj}"
        if obj not in state.objects:
            return state.copy(), False, ErrorCode.STATE_TRANSITION_ERROR, f"{obj} 不在场景中，无法 press"
        nxt = state.copy()
        nxt.pressed.add(obj)
        return nxt, True, None, ""

    # PLACE
    if state.held_object(arm) != obj:
        return state.copy(), False, ErrorCode.OBJECT_NOT_HELD, f"{arm} 未持有 {obj}，无法 place"
    tgt = action.target_id
    if tgt is None or tgt not in targets or tgt in state.holding.values():
        return state.copy(), False, ErrorCode.TARGET_NOT_FOUND, f"目标 {tgt} 不可用"
    nxt = state.copy()
    del nxt.holding[arm]
    nxt.at[obj] = tgt
    return nxt, True, None, ""
