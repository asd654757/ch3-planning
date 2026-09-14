#!/usr/bin/env python3
"""Summarize the execution-state recovery benchmark by arm and perturbation."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


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
        "schema_version": "2026-09-14-state-recovery-analysis-v1",
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
