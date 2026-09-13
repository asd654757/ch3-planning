"""Deterministic closed-world checks shared by planners and repairers."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ch3.goal.infeasibility import logical_conflict_infeasibility
from ch3.schema.model_plan import ModelPlan


def fact_object_ids(fact: str) -> list[str]:
    """Return object/target IDs referenced by a supported goal fact."""
    if not isinstance(fact, str) or not fact.endswith(")"):
        return []
    if fact.startswith("pressed("):
        return [fact[len("pressed("):-1]]
    if fact.startswith(("on(", "pushed_to(")):
        prefix = "on(" if fact.startswith("on(") else "pushed_to("
        parts = [part.strip() for part in fact[len(prefix):-1].split(",", 1)]
        return parts if len(parts) == 2 else []
    return []


def closed_world_infeasibility(
    task: Mapping[str, Any],
    *,
    plan: Optional[ModelPlan] = None,
    reason: Optional[str] = None,
) -> Optional[str]:
    """Reject action or goal references outside the declared visible set."""
    visible = set(map(str, task["objects"]))
    conflict_reason = logical_conflict_infeasibility(task)
    if conflict_reason is not None:
        return conflict_reason
    if plan is not None:
        for action in plan.actions:
            for value in (action.object_id, action.target_id):
                if value and value != "table" and value not in visible:
                    return f"object {value} is not in the visible list"
    if reason is not None:
        return reason
    for fact in task.get("goal", {}).get("facts", []):
        for object_id in fact_object_ids(fact):
            if object_id != "table" and object_id not in visible:
                return f"goal object {object_id} is not in the visible list"
    return None
