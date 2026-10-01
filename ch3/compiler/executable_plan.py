"""ModelPlan -> ExecutablePlan 编译（自动补 policy_id / primitive）。

ExecutablePlan 中的策略名一律来自能力注册表，模型生成内容不能直接进入
执行接口（信任边界）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import ModelPlan, ModelPlanAction


@dataclass
class ExecutableStep:
    step_id: int
    policy_id: str          # 注册表路由名称；历史 adflow_* 不证明实际后端
    primitive: str          # 后端适配器使用的原语名
    args: dict[str, Any]
    source_skill: str       # pick / place


@dataclass
class ExecutablePlan:
    steps: list[ExecutableStep] = field(default_factory=list)

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {
                "step_id": s.step_id,
                "policy_id": s.policy_id,
                "primitive": s.primitive,
                "args": s.args,
            }
            for s in self.steps
        ]


def compile_plan(plan: ModelPlan, registry: CapabilityRegistry) -> ExecutablePlan:
    steps: list[ExecutableStep] = []
    for a in plan.actions:
        args: dict[str, Any] = {"object_id": a.object_id, "arm": a.arm.value}
        if a.target_id is not None:
            args["target_id"] = a.target_id
        steps.append(
            ExecutableStep(
                step_id=a.step_id,
                policy_id=registry.policy_for(a.skill),
                primitive=registry.primitive_for(a.skill),
                args=args,
                source_skill=a.skill.value,
            )
        )
    return ExecutablePlan(steps)
