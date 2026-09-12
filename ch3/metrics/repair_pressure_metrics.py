"""Metrics for deterministic repair-pressure collections.

The pressure runner records one JSONL row for every
``(task, seed, pressure_type, repair_mode)``.  It also records a
``pressure_source`` row when the VLM fails to produce a valid baseline.  This
module reports repair-only metrics from the controllable invalid plans, plus
baseline-availability separately.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional


def load_records(path: str | Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def compute_pressure_metrics(
    records: list[dict[str, Any]],
    *,
    pressure_type: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    rows = [r for r in records if r.get("record_type") == "repair_pressure"]
    if pressure_type is not None:
        rows = [r for r in rows if r.get("pressure_type") == pressure_type]
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_mode[row.get("repair_mode", "?")].append(row)

    result: dict[str, dict[str, Any]] = {}
    for mode, mode_rows in sorted(by_mode.items()):
        n = len(mode_rows)
        valid = sum(bool(r.get("valid")) for r in mode_rows)
        goal = sum(bool(r.get("goal_satisfied")) for r in mode_rows)
        valid_but_goal_fail = sum(
            bool(r.get("valid")) and not bool(r.get("goal_satisfied")) for r in mode_rows
        )
        result[mode] = {
            "n": n,
            "CRR": valid / n if n else None,
            "GSR_after_repair": goal / n if n else None,
            "VGF": valid_but_goal_fail / n if n else None,
            "valid_after": valid,
            "goal_after": goal,
            "pass_but_wrong": sum(bool(r.get("pass_but_wrong")) for r in mode_rows),
            "accepted": sum(bool(r.get("accepted")) for r in mode_rows),
            "parse_errors": sum(bool(r.get("parse_error")) for r in mode_rows),
            "rejections": dict(
                Counter(r.get("reject_reason") for r in mode_rows if r.get("reject_reason"))
            ),
            "failure_codes": dict(
                Counter(r.get("error_code") for r in mode_rows if not r.get("valid") and r.get("error_code"))
            ),
        }
    return result


def compute_pressure_type_metrics(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = [r for r in records if r.get("record_type") == "repair_pressure"]
    result: dict[str, dict[str, Any]] = {}
    for pressure_type in sorted({r.get("pressure_type", "?") for r in rows}):
        result[pressure_type] = compute_pressure_metrics(records, pressure_type=pressure_type)
    return result


def compute_baseline_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    source_rows = [r for r in records if r.get("record_type") == "pressure_source"]
    valid_source = [r for r in records if r.get("pressure_source_valid")]
    tasks = {(r.get("task_id"), r.get("seed")) for r in records}
    valid_tasks = {(r.get("task_id"), r.get("seed")) for r in valid_source}
    return {
        "task_seed_slices": len(tasks),
        "valid_baseline_slices": len(valid_tasks),
        "invalid_baseline_records": sum(r.get("record_type") == "pressure_source" for r in records),
        "baseline_valid_rate": len(valid_tasks) / len(tasks) if tasks else None,
    }


def compute_all(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "baseline": compute_baseline_metrics(records),
        "overall_by_mode": compute_pressure_metrics(records),
        "by_pressure_type": compute_pressure_type_metrics(records),
    }


def format_summary(result: dict[str, Any]) -> str:
    lines = ["=== Repair-pressure summary ==="]
    baseline = result["baseline"]
    rate = baseline.get("baseline_valid_rate")
    lines.append(
        f"baseline: {baseline['valid_baseline_slices']}/{baseline['task_seed_slices']} valid"
        + (f" ({rate:.1%})" if rate is not None else "")
    )
    lines.append("")
    lines.append("== Overall by mode ==")
    for mode, m in result["overall_by_mode"].items():
        crr = m["CRR"]
        gsr = m["GSR_after_repair"]
        vgf = m["VGF"]
        lines.append(
            f"{mode}: n={m['n']} CRR={crr:.1%} GSR_after={gsr:.1%} VGF={vgf:.1%} "
            f"pbw={m['pass_but_wrong']} accepted={m['accepted']}"
        )
    lines.append("")
    lines.append("== By pressure type ==")
    for pressure_type, by_mode in result["by_pressure_type"].items():
        lines.append(f"[{pressure_type}]")
        for mode, m in by_mode.items():
            lines.append(
                f"  {mode}: n={m['n']} CRR={m['CRR']:.1%} GSR_after={m['GSR_after_repair']:.1%} "
                f"VGF={m['VGF']:.1%} codes={m['failure_codes']}"
            )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = compute_all(load_records(args.collection))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
