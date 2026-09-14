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
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("record_type") != "state_recovery":
            continue
        groups[(row["baseline"], row["perturbation_type"])].append(row)

    summary: dict[str, Any] = {
        "schema_version": "2026-09-14-state-recovery-analysis-v1",
        "input": args.input,
        "points": len([r for r in rows if r.get("record_type") == "state_recovery"]),
        "summary_by_arm": {},
        "by_arm_perturbation": {},
    }
    arm_rows = defaultdict(list)
    for (arm, perturbation), group in sorted(groups.items()):
        def rate(field: str) -> float | None:
            return sum(bool(r.get(field)) for r in group) / len(group) if group else None
        item = {
            "points": len(group),
            "valid_rate": rate("valid"),
            "goal_satisfied_rate": rate("goal_satisfied"),
            "recovery_success_rate": rate("recovery_success"),
            "pass_but_wrong_rate": rate("pass_but_wrong"),
            "prefix_mutation_rate": rate("prefix_mutation"),
            "model_calls": sum(int(r.get("model_calls") or 0) for r in group),
            "avg_model_calls": sum(int(r.get("model_calls") or 0) for r in group) / len(group),
            "unnecessary_action_count": sum(int(r.get("unnecessary_action_count") or 0) for r in group),
            "avg_suffix_actions": sum(int(r.get("suffix_action_count") or 0) for r in group) / len(group),
        }
        summary["by_arm_perturbation"][f"{arm}:{perturbation}"] = item
        arm_rows[arm].extend(group)

    for arm, group in sorted(arm_rows.items()):
        def rate(field: str) -> float | None:
            return sum(bool(r.get(field)) for r in group) / len(group) if group else None
        summary["summary_by_arm"][arm] = {
            "points": len(group),
            "valid_rate": rate("valid"),
            "goal_satisfied_rate": rate("goal_satisfied"),
            "recovery_success_rate": rate("recovery_success"),
            "pass_but_wrong_rate": rate("pass_but_wrong"),
            "prefix_mutation_rate": rate("prefix_mutation"),
            "model_calls": sum(int(r.get("model_calls") or 0) for r in group),
            "avg_model_calls": sum(int(r.get("model_calls") or 0) for r in group) / len(group),
            "unnecessary_action_count": sum(int(r.get("unnecessary_action_count") or 0) for r in group),
            "avg_suffix_actions": sum(int(r.get("suffix_action_count") or 0) for r in group) / len(group),
        }

    output = Path(args.output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite analysis output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
