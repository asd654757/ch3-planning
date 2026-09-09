"""能力注册表：技能闭集 + policy 映射（pick/place -> AD-Flow grasp/place）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ch3.schema.model_plan import Arm, Skill


class CapabilityRegistry:
    """加载自 YAML 的能力闭集。

    提供：
    - 技能是否注册（UNREGISTERED_SKILL 判定）
    - 手臂是否可用
    - 放置目标是否合法（table 或场景对象，见 object_validator）
    - 技能 -> 底层 policy（adflow_grasp / adflow_place）映射，供 compiler 使用
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._arms: set[str] = set(data.get("arms", []))
        self._caps: dict[str, dict[str, Any]] = data.get("capabilities", {})
        self._special_targets: dict[str, dict[str, Any]] = data.get("special_targets", {})
        # 冻结设计：注册表不做运行时再发现；缺技能 = 非法（E03）

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CapabilityRegistry":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data)

    @property
    def skills(self) -> set[str]:
        return set(self._caps)

    @property
    def arms(self) -> set[str]:
        return set(self._arms)

    @property
    def special_targets(self) -> set[str]:
        return set(self._special_targets)

    def has_skill(self, skill: Skill | str) -> bool:
        return str(skill.value if isinstance(skill, Skill) else skill) in self._caps

    def has_arm(self, arm: Arm | str) -> bool:
        return str(arm.value if isinstance(arm, Arm) else arm) in self._arms

    def is_special_target(self, target_id: str) -> bool:
        return target_id in self._special_targets

    def policy_for(self, skill: Skill | str) -> str:
        key = skill.value if isinstance(skill, Skill) else skill
        return self._caps[key]["policy"]

    def primitive_for(self, skill: Skill | str) -> str:
        key = skill.value if isinstance(skill, Skill) else skill
        return self._caps[key]["primitive"]

    def required_args(self, skill: Skill | str) -> list[str]:
        key = skill.value if isinstance(skill, Skill) else skill
        return list(self._caps[key]["args"])


def load_registry(path: str | Path = "config/capability_registry.yaml") -> CapabilityRegistry:
    return CapabilityRegistry.from_yaml(path)
