"""离散世界状态（hand_empty / holding / on_table / on）。

状态表示刻意精简：
- holding: arm -> object_id（空手则不记录）
- at:      object_id -> 所在表面 id（'table' 或另一物体 id）

事实串（facts）与 GoalSpec 共用同一格式，支持 goal ⊆ S_T 判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class WorldState:
    objects: set[str] = field(default_factory=set)
    at: dict[str, str] = field(default_factory=dict)          # obj -> surface id
    holding: dict[str, str] = field(default_factory=dict)      # arm -> obj
    table_id: str = "table"

    @classmethod
    def table_scene(cls, object_ids: Iterable[str]) -> "WorldState":
        """初始场景：所有对象都在 table 上，双臂空。"""
        objs = list(object_ids)
        return cls(objects=set(objs), at={o: "table" for o in objs})

    def copy(self) -> "WorldState":
        return WorldState(
            objects=set(self.objects),
            at=dict(self.at),
            holding=dict(self.holding),
            table_id=self.table_id,
        )

    def arm_empty(self, arm: str) -> bool:
        return arm not in self.holding

    def held_object(self, arm: str) -> str | None:
        return self.holding.get(arm)

    def location_of(self, obj: str) -> str | None:
        if obj in self.holding.values():
            return None  # 正在被手持，不在任何表面
        return self.at.get(obj)

    def is_on_table(self, obj: str) -> bool:
        return obj in self.at and self.at[obj] == self.table_id

    def facts(self) -> set[str]:
        """把状态展开为事实串集合（goal ⊆ S_T 的 S_T 侧）。"""
        out: set[str] = set()
        for arm in sorted(self.holding):
            out.add(f"holding({arm}, {self.holding[arm]})")
        held = set(self.holding.values())
        for obj in sorted(self.objects):
            if obj in held:
                continue
            loc = self.at.get(obj)
            if loc is not None:
                out.add(f"on({obj}, {loc})")
        # 空手事实只对注册过的手臂生成；手臂集合需外部传入 arms
        return out

    def empty_hand_facts(self, arms: Iterable[str]) -> set[str]:
        return {f"hand_empty({a})" for a in arms if a not in self.holding}
