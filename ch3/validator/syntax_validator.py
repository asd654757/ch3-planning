"""第一层：结构检查（schema/字段/步号连续性）。"""

from __future__ import annotations

from ch3.schema.model_plan import ModelPlan
from ch3.errors import ErrorCode


def check_plan(plan: ModelPlan) -> tuple[Optional[int], Optional[str]]:
    """返回 (first_invalid_step, message)；全过返回 (None, None)。

    字段类型/枚举已由 pydantic 保证（解析失败属于 E01，由调用方分类）；
    这里检查 pydantic 管不到的结构约束：step_id 必须从 1 连续递增。
    """
    expected = 1
    for a in plan.actions:
        if a.step_id != expected:
            return a.step_id, f"step_id 不连续：期望 {expected}，得到 {a.step_id}"
        expected += 1
    return None, None
