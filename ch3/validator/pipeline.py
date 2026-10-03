"""Validator：四层串行 + 首错定位（纯函数评测器 evaluator）。

所有臂（B0/B1/B2a/B2b）的 EPR/GSR 都用同一实现计算（冻结口径）。
只做评测，不做任何改写——改写在阶段 2（R2 前缀保留修正）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ch3.capability.registry import CapabilityRegistry
from ch3.schema.model_plan import ModelPlan
from ch3.state.world_state import WorldState
from ch3.validator import capability_validator, object_validator, state_validator, syntax_validator
from ch3.errors import ErrorCode
from ch3.validator.result import ValidationResult


@dataclass
class Validator:
    """四层确定性校验器。

    - scene_objects: 闭世界场景对象 id 集合
    - registry: 能力注册表
    - special_targets: 额外合法放置位（如 table）
    """

    scene_objects: set[str]
    registry: CapabilityRegistry
    special_targets: Optional[set[str]] = None
    pick_surfaces: Optional[set[str]] = None  # opt-in backend-supported flat surfaces; legacy defaults unchanged

    def __post_init__(self) -> None:
        if self.special_targets is None:
            self.special_targets = self.registry.special_targets

    @property
    def valid_targets(self) -> set[str]:
        return set(self.scene_objects) | set(self.special_targets or set())

    def _prefix_state(self, prefix: list, initial_state: WorldState) -> Optional[WorldState]:
        """Simulate the validated prefix to expose the post-prefix state.

        Even when validation fails at the object/capability layer, the locked
        prefix has execution semantics ("these steps would have run"), so
        downstream state-aware repair needs the world state after the prefix.
        If the prefix itself contains a state error, the state reached before
        that error is returned.
        """
        if not prefix:
            return None
        _ok, _bad, _code, _msg, final_state = state_validator.simulate_plan(
            ModelPlan(actions=list(prefix)), initial_state, valid_targets=self.valid_targets, pick_surfaces=self.pick_surfaces
        )
        return final_state

    # ---------- 第三层能力 ----------
    def _capability_error(self, action) -> tuple[Optional[str], str]:
        code, msg = capability_validator.check_action(action, self.registry)
        if code is not None:
            return code.value, msg
        return None, ""

    def validate(self, plan: ModelPlan, initial_state: WorldState) -> ValidationResult:
        """四层校验，返回首错定位与合法前缀。"""
        # 层1 syntax（整段）
        bad_step, msg = syntax_validator.check_plan(plan)
        if bad_step is not None:
            return ValidationResult(
                valid=False,
                first_invalid_step=bad_step,
                error_code=ErrorCode.SCHEMA_ERROR,
                message=msg,
                layer="syntax",
            )

        # 层2/3 object+capability：逐动作定位首个非 state 错误
        prefix: list = []
        for action in plan.actions:
            code, msg = object_validator.check_action(action, self.scene_objects, self.special_targets or set())
            if code is not None:
                return ValidationResult(
                    valid=False, first_invalid_step=action.step_id, error_code=code,
                    message=msg, layer="object", validated_prefix=list(prefix),
                    final_state=self._prefix_state(prefix, initial_state),
                )
            code, msg = capability_validator.check_action(action, self.registry)
            if code is not None:
                return ValidationResult(
                    valid=False, first_invalid_step=action.step_id, error_code=code,
                    message=msg, layer="capability", validated_prefix=list(prefix),
                    final_state=self._prefix_state(prefix, initial_state),
                )
            prefix.append(action)

        # 层4 state：状态模拟推进（依赖顺序）
        ok, bad_step, code, msg, final_state = state_validator.simulate_plan(
            plan, initial_state, valid_targets=self.valid_targets, pick_surfaces=self.pick_surfaces
        )
        if not ok:
            # 找到首个 state 层失败：合法前缀 = 失败步之前的所有动作
            valid_prefix = [a for a in plan.actions if a.step_id < (bad_step or 0)]
            return ValidationResult(
                valid=False, first_invalid_step=bad_step, error_code=code,
                message=msg, layer="state", validated_prefix=valid_prefix,
                final_state=final_state,
            )
        # 语义完整性约束：ModelPlan 是完整规划，不能以未释放的 pick 结束。
        # 该检查放在状态模拟之后，避免掩盖 object/capability/state 的首错。
        if final_state.holding:
            last_action = plan.actions[-1] if plan.actions else None
            return ValidationResult(
                valid=False,
                first_invalid_step=last_action.step_id if last_action else None,
                error_code=ErrorCode.SCHEMA_ERROR,
                message="计划不完整：每个 pick 必须立即跟一个对应的 place，不能以 holding 状态结束",
                layer="syntax",
                validated_prefix=list(plan.actions),
                final_state=final_state,
            )
        return ValidationResult(
            valid=True,
            validated_prefix=list(plan.actions),
            final_state=final_state,
        )
