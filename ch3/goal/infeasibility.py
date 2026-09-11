"""Deterministic goal-level infeasibility checks shared by all planners."""

from __future__ import annotations

from typing import Any, Mapping


def logical_conflict_infeasibility(task: Mapping[str, Any]) -> str | None:
    """Return a reason when one object is required on multiple surfaces.

    The current frozen goal language only contains ``on(obj, surface)`` facts.
    A final state can satisfy at most one surface for a visible object, so
    duplicate surfaces for the same object are logically contradictory.  The
    check is deliberately deterministic and independent of the VLM response.
    """
    surfaces_by_object: dict[str, set[str]] = {}
    for fact in task.get("goal", {}).get("facts", []):
        if not isinstance(fact, str) or not fact.startswith("on(") or not fact.endswith(")"):
            continue
        inner = fact[3:-1]
        parts = [part.strip() for part in inner.split(",", 1)]
        if len(parts) != 2:
            continue
        object_id, surface = parts
        surfaces_by_object.setdefault(object_id, set()).add(surface)

    for object_id, surfaces in surfaces_by_object.items():
        if len(surfaces) > 1:
            return (
                f"goal requires object {object_id} to be simultaneously on "
                f"multiple surfaces: {', '.join(sorted(surfaces))}"
            )
    return None
