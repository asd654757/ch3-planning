"""Execution Feedback Interface（冻结版）。

真机侧：executor.execute(action) -> SUCCESS | FAILED | UNKNOWN，
结合夹爪状态 / 物体位置 / AD-Flow 返回 / 简单视觉确认更新离散状态；
FAILED/UNKNOWN -> stop 剩余计划并归因，不允许默认按成功推进。

本骨架不实现视觉 success detector（评审决定不扩范围）；
SimulatedFeedbackProvider 用于离线：把"模拟器推进"当作执行反馈。
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from ch3.schema.model_plan import ModelPlanAction
from ch3.state.simulator import step as simulate_step
from ch3.state.world_state import WorldState


class ExecutionFeedback(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ExecutionFeedbackProvider(Protocol):
    """真机 Adapter 需实现的接口（阶段 4 接 AD-Flow）。"""

    def execute(self, state: WorldState, action: ModelPlanAction) -> ExecutionFeedback:
        """执行一个原子动作并返回执行反馈。"""
        ...


class SimulatedFeedbackProvider:
    """离线执行反馈：等价于状态模拟器推进结果。"""

    def __init__(self, valid_targets: set[str] | None = None) -> None:
        self._targets = valid_targets

    def execute(self, state: WorldState, action: ModelPlanAction) -> ExecutionFeedback:
        _, ok, _, _ = simulate_step(state, action, valid_targets=self._targets)
        return ExecutionFeedback.SUCCESS if ok else ExecutionFeedback.FAILED
