#!/usr/bin/env python3
"""Summarize the execution-state recovery benchmark by arm and perturbation."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _suffix_actions(row: dict[str, Any]) -> list[dict[str, Any]]:
    plan = row.get("model_plan") or {}
    actions = plan.get("actions") or []
    prefix_len = len(row.get("executed_prefix_step_ids") or [])
    return list(actions[prefix_len:])


def _state_conditioned_executable(row: dict[str, Any]) -> bool:
    """Whether the repaired suffix validates from the observed state."""
    if "repair_suffix_executable" in row:
        return bool(row["repair_suffix_executable"])
    return bool(row.get("goal_satisfied") or row.get("pass_but_wrong"))


def _semantic_action_key(action: dict[str, Any]) -> tuple[Any, ...]:
    return (
        action.get("skill"),
        action.get("object_id"),
        action.get("target_id"),
        action.get("arm"),
    )


def _stale_replay(row: dict[str, Any]) -> bool:
    """Detect semantic replay whose planned effect is already in S_t."""
    facts = set(row.get("observed_state_facts") or [])
    plan = row.get("model_plan") or {}
    actions = plan.get("actions") or []
    prefix_len = len(row.get("executed_prefix_step_ids") or [])
    prefix_keys = {_semantic_action_key(a) for a in actions[:prefix_len]}
    for action in _suffix_actions(row):
        if _semantic_action_key(action) not in prefix_keys:
            continue
        skill = action.get("skill")
        arm = action.get("arm")
        object_id = action.get("object_id")
        target_id = action.get("target_id")
        if skill == "pick" and f"holding({arm}, {object_id})" in facts:
            return True
        if skill == "place" and f"on({object_id}, {target_id})" in facts:
            return True
        if skill == "push" and f"pushed_to({object_id}, {target_id})" in facts:
            return True
        if skill == "press" and f"pressed({object_id})" in facts:
            return True
    return False


def _state_conflicting_action(row: dict[str, Any]) -> bool:
    """Detect suffix actions that violate the observed or evolving state."""
    facts = set(row.get("observed_state_facts") or [])
    for action in _suffix_actions(row):
        skill = action.get("skill")
        arm = action.get("arm")
        object_id = action.get("object_id")
        target_id = action.get("target_id")
        held_fact = next(
            (fact for fact in facts if fact.startswith(f"holding({arm}, ")),
            None,
        )
        holds_this_object = f"holding({arm}, {object_id})" in facts
        if skill in {"pick", "push", "press"} and held_fact is not None:
            return True
        if skill == "place" and not holds_this_object:
            return True

        # Advance the local state so that a valid pick is allowed to provide
        # the holding precondition for its corresponding place.
        if skill == "pick":
            facts.discard(held_fact)
            facts.add(f"holding({arm}, {object_id})")
        elif skill == "place":
            facts.discard(f"holding({arm}, {object_id})")
            facts.add(f"on({object_id}, {target_id})")
        elif skill == "push":
            facts.add(f"pushed_to({object_id}, {target_id})")
        elif skill == "press":
            facts.add(f"pressed({object_id})")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()]
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("record_type") != "state_recovery":
            continue
        family = (
            "pick_place" if "pick_place" in row["source_task_id"]
            else "push" if "push" in row["source_task_id"]
            else "press"
        )
        groups[(row["baseline"], row["perturbation_type"], family)].append(row)

    summary: dict[str, Any] = {
        "schema_version": "2026-09-14-state-recovery-analysis-v2",
        "input": args.input,
        "points": len([r for r in rows if r.get("record_type") == "state_recovery"]),
        "summary_by_arm": {},
        "by_arm_perturbation": {},
        "by_arm_task_family": {},
        "by_arm_perturbation_family": {},
    }
    arm_rows = defaultdict(list)
    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        def rate(field: str) -> float | None:
            return sum(bool(r.get(field)) for r in group) / len(group) if group else None
        perturbed = [r for r in group if r.get("perturbation_type") != "nominal_state"]
        model_calls = sum(int(r.get("model_calls") or 0) for r in group)
        total_tokens = sum(int(r.get("total_tokens") or 0) for r in group)
        input_tokens = sum(int(r.get("input_tokens") or 0) for r in group)
        output_tokens = sum(int(r.get("output_tokens") or 0) for r in group)
        fallbacks = sum(
            bool(r.get("baseline") == "ROUTED" and r.get("fallback_triggered"))
            for r in group
        )
        item = {
            "points": len(group),
            "perturbed_points": len(perturbed),
            "observed_validation_valid_rate": rate("valid"),
            "goal_satisfied_rate": rate("goal_satisfied"),
            "recovery_success_rate": rate("recovery_success"),
            "non_nominal_recovery_success_rate": (
                sum(bool(r.get("recovery_success")) for r in perturbed) / len(perturbed)
                if perturbed else None
            ),
            "pass_but_wrong_rate": rate("pass_but_wrong"),
            "prefix_mutation_rate": rate("prefix_mutation"),
            "model_calls": model_calls,
            "avg_model_calls": model_calls / len(group),
            "fallbacks": fallbacks,
            "fallback_rate": fallbacks / len(group),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "avg_total_tokens_per_model_call": total_tokens / model_calls if model_calls else None,
            "unnecessary_action_count": sum(int(r.get("unnecessary_action_count") or 0) for r in group),
            "avg_suffix_actions": sum(int(r.get("suffix_action_count") or 0) for r in group) / len(group),
        }
        executable = [r for r in group if _state_conditioned_executable(r)]
        if executable:
            overheads = [
                max(0, int(r.get("suffix_action_count") or 0) - int(r.get("minimal_suffix_action_count") or 0))
                for r in executable
            ]
            normalized = [
                overhead / max(1, int(r.get("minimal_suffix_action_count") or 0))
                for r, overhead in zip(executable, overheads)
            ]
            item.update({
                "state_conditioned_executable_points": len(executable),
                "state_conditioned_executable_rate": len(executable) / len(group),
                "avg_suffix_actions_on_executable": sum(
                    int(r.get("suffix_action_count") or 0) for r in executable
                ) / len(executable),
                "avg_minimal_suffix_actions": sum(
                    int(r.get("minimal_suffix_action_count") or 0) for r in executable
                ) / len(executable),
                "action_overhead_total_on_executable": sum(overheads),
                "avg_action_overhead_on_executable": sum(overheads) / len(executable),
                "avg_normalized_action_overhead_on_executable": sum(normalized) / len(normalized),
            })
        else:
            item.update({
                "state_conditioned_executable_points": 0,
                "state_conditioned_executable_rate": 0.0,
                "avg_suffix_actions_on_executable": None,
                "avg_minimal_suffix_actions": None,
                "action_overhead_total_on_executable": None,
                "avg_action_overhead_on_executable": None,
                "avg_normalized_action_overhead_on_executable": None,
            })
        stale = [r for r in group if _stale_replay(r)]
        item.update({
            "stale_replay_points": len(stale),
            "stale_replay_rate": len(stale) / len(group),
        })
        conflicting = [r for r in group if _state_conflicting_action(r)]
        item.update({
            "state_conflicting_action_points": len(conflicting),
            "state_conflicting_action_rate": len(conflicting) / len(group),
        })
        return item

    for (arm, perturbation, family), group in sorted(groups.items()):
        summary["by_arm_perturbation_family"][f"{arm}:{perturbation}:{family}"] = summarize(group)
        arm_rows[arm].extend(group)
    perturb_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (arm, perturbation, _family), group in groups.items():
        perturb_groups[(arm, perturbation)].extend(group)
    for (arm, perturbation), group in sorted(perturb_groups.items()):
        summary["by_arm_perturbation"][f"{arm}:{perturbation}"] = summarize(group)
    family_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (arm, _perturbation, family), group in groups.items():
        family_groups[(arm, family)].extend(group)
    for (arm, family), group in sorted(family_groups.items()):
        summary["by_arm_task_family"][f"{arm}:{family}"] = summarize(group)

    for arm, group in sorted(arm_rows.items()):
        summary["summary_by_arm"][arm] = summarize(group)

    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite analysis output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
