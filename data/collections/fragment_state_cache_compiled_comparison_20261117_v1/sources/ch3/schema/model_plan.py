"""ModelPlan 结构（冻结版 2026-09-08）。

ModelPlan = VLM 生成的"任务级语义计划"，只含任务语义字段：
skill / object_id / target_id / arm / step_id。
它不可直接下发；须经 Validator + Compiler 变成 ExecutablePlan。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Skill(str, Enum):
    """机器人技能闭集（任务/计划层）。"""

    PICK = "pick"
    PLACE = "place"
    PUSH = "push"
    PRESS = "press"


class Arm(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class ModelPlanAction(BaseModel):
    """单个语义动作。

    字段完备性规则由 capability 校验强制（不是 pydantic 报错），以区分
    "格式非法"与"capability 非法"两类错误：
    - pick:  需要 object_id；不应提供 target_id
    - place: 需要 object_id 与 target_id
    """

    step_id: int = Field(ge=1, description="从 1 开始的步骤号")
    skill: Skill
    object_id: str = Field(..., description="被操作对象 id（闭世界集合内）")
    target_id: Optional[str] = Field(None, description="放置目标（place 必填）：table 或场景对象 id")
    arm: Arm


class ModelPlan(BaseModel):
    """一次 VLM 输出的完整计划。"""

    actions: list[ModelPlanAction] = Field(..., min_length=1)


class GoalSpec(BaseModel):
    """任务目标，表示为事实串集合；满足条件为 goal ⊆ S_T。

    事实串格式与 WorldState.facts() 一致，例如：
      on(red_cube_0, table) / on(red_cube_0, tray_1)
      holding(left, red_cube_0) / hand_empty(left)
    """

    facts: list[str] = Field(..., min_length=1)
