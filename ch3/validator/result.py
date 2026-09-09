"""校验结果：逐层 + 首错定位 + 合法前缀 + 终点状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ch3.schema.model_plan import ModelPlanAction
from ch3.state.world_state import WorldState
from ch3.errors import ErrorCode


@dataclass
class ValidationResult:
    """一次完整校验的输出。

    - valid: 计划可执行（四层全过）
    - first_invalid_step: 首个非法动作的 step_id（1-based），无则 None
    - error_code / message: 首错原因
    - layer: 出错层（syntax/object/capability/state）
    - validated_prefix: 通过的前缀（供 R2 前缀保留修正使用）
    - final_state: 前缀终点状态（供修复 prompt 与 goal 判定使用）
    """

    valid: bool = True
    first_invalid_step: Optional[int] = None
    error_code: Optional[ErrorCode] = None
    message: str = ""
    layer: Optional[str] = None
    validated_prefix: list[ModelPlanAction] = field(default_factory=list)
    final_state: Optional[WorldState] = None
