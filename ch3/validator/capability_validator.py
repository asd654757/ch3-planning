"""第三层：Capability 检查（技能注册 + 参数完备 + 手臂有效）。"""

from __future__ import annotations

from typing import Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import ModelPlanAction, Skill
from ch3.errors import ErrorCode


def check_action(
    action: ModelPlanAction,
    registry: CapabilityRegistry,
) -> tuple[Optional[ErrorCode], str]:
    if not registry.has_skill(action.skill):
        return ErrorCode.UNREGISTERED_SKILL, f"技能 {action.skill.value} 未注册"
    if not registry.has_arm(action.arm):
        return ErrorCode.SCHEMA_ERROR, f"手臂 {action.arm.value} 未注册"  # 结构性问题

    # 参数完备性
    if action.skill == Skill.PLACE and action.target_id is None:
        return ErrorCode.MISSING_PARAMETER, "place 缺少 target_id"
    if action.skill == Skill.PICK and action.target_id is not None:
        return ErrorCode.MISSING_PARAMETER, "pick 不应携带 target_id"
    return None, ""
