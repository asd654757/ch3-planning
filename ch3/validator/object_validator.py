"""第二层：场景对象闭世界检查。"""

from __future__ import annotations

from typing import Optional

from ch3.schema.model_plan import ModelPlanAction, Skill
from ch3.errors import ErrorCode


def check_action(
    action: ModelPlanAction,
    scene_objects: set[str],
    special_targets: set[str],
) -> tuple[Optional[ErrorCode], str]:
    """对象存在性 + 放置目标存在性。合法返回 (None, "")。"""
    if action.object_id not in scene_objects:
        return ErrorCode.UNKNOWN_OBJECT, f"对象 {action.object_id} 不在闭世界场景中"
    if action.skill in {Skill.PLACE, Skill.PUSH}:
        tgt = action.target_id
        if tgt is None:
            return None, ""  # 缺失参数由 capability 层报 E04
        if tgt not in scene_objects and tgt not in special_targets:
            return ErrorCode.UNKNOWN_OBJECT, f"放置目标 {tgt} 不在闭世界场景中"
    return None, ""
