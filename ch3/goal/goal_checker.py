"""目标满足判定：goal ⊆ S_T。

Repair Success 的严格定义 = 修复后通过完整校验 ∧ goal ⊆ S_T；
只过校验不过目标 => pass-but-wrong。
"""

from __future__ import annotations

from ch3.schema.model_plan import GoalSpec
from ch3.state.world_state import WorldState


def goal_satisfied(state: WorldState, goal: GoalSpec, arms: set[str]) -> bool:
    """goal.facts ⊆ 状态事实（含空手事实）。"""
    facts: set[str] = state.facts() | state.empty_hand_facts(arms)
    return set(goal.facts).issubset(facts)
