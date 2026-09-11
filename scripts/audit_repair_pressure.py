#!/usr/bin/env python3
"""Audit a repair-pressure collection for oracle leakage and paired outcomes."""

from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
from typing import Any


MODES = ("R0", "R1", "R2")
PAIRS = (("R0", "R1"), ("R0", "R2"), ("R1", "R2"))


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def _extract_original_plan(prompt: str) -> dict[str, Any] | None:
    """Extract the JSON object following ``original_plan`` in a prompt."""
    marker = '"original_plan":'
    start = prompt.find(marker)
    if start < 0:
        return None
    pos = start + len(marker)
    while pos < len(prompt) and prompt[pos].isspace():
        pos += 1
    if pos >= len(prompt) or prompt[pos] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx in range(pos, len(prompt)):
        char = prompt[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(prompt[pos : idx + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _mcnemar(a: list[bool], b: list[bool]) -> dict[str, Any]:
    n00 = a_only = b_only = n11 = 0
    for left, right in zip(a, b):
        if left and not right:
            a_only += 1
        elif not left and right:
            b_only += 1
        elif not left and not right:
            n00 += 1
        else:
            n11 += 1

    n = a_only + b_only
    if n == 0:
        p = 1.0
    else:
        k = min(a_only, b_only)
        p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / (2**n))
    return {"n00": n00, "a_only": a_only, "b_only": b_only, "n11": n11, "p": p}


def audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    repairs = [row for row in rows if row.get("record_type") == "repair_pressure"]
    by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in MODES}
    for row in repairs:
        mode = row.get("repair_mode")
        if mode in by_mode:
            by_mode[mode].append(row)

    exact: dict[str, dict[str, Any]] = {}
    prompt_audit: dict[str, dict[str, Any]] = {}
    for mode, rows_for_mode in by_mode.items():
        n = len(rows_for_mode)
        exact_source = sum(
            row.get("model_plan") == row.get("pressure_source_plan")
            for row in rows_for_mode
        )
        exact_stress = sum(row.get("model_plan") == row.get("stress_plan") for row in rows_for_mode)
        original_is_source = 0
        original_is_stress = 0
        for row in rows_for_mode:
            original = _extract_original_plan(row.get("prompt", ""))
            original_is_source += int(original == row.get("pressure_source_plan"))
            original_is_stress += int(original == row.get("stress_plan"))
        exact[mode] = {
            "n": n,
            "exact_source": exact_source,
            "exact_source_rate": exact_source / n if n else 0.0,
            "exact_stress": exact_stress,
            "exact_stress_rate": exact_stress / n if n else 0.0,
        }
        prompt_audit[mode] = {
            "n": n,
            "original_plan_is_source": original_is_source,
            "original_plan_is_stress": original_is_stress,
        }

    paired: dict[tuple[str, int, str], dict[str, bool]] = collections.defaultdict(dict)
    for row in repairs:
        key = (row.get("task_id"), row.get("seed"), row.get("pressure_type"))
        paired[key][row["repair_mode"]] = bool(row.get("valid"))

    mcnemar = {}
    for a, b in PAIRS:
        keys = [key for key in paired if a in paired[key] and b in paired[key]]
        left = [paired[key][a] for key in keys]
        right = [paired[key][b] for key in keys]
        mcnemar[f"{a}_vs_{b}_valid"] = _mcnemar(left, right)

    return {
        "repair_records": len(repairs),
        "exact_match": exact,
        "prompt_audit": prompt_audit,
        "mcnemar_valid": mcnemar,
    }


def format_report(result: dict[str, Any], path: Path) -> str:
    lines = [
        f"# Repair-pressure oracle audit: {path.name}",
        "",
        f"Repair records: **{result['repair_records']}**",
        "",
        "## Exact-match and prompt audit",
        "",
        "| Mode | n | exact source | exact stress | prompt original=source | prompt original=stress |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        e = result["exact_match"][mode]
        p = result["prompt_audit"][mode]
        lines.append(
            f"| {mode} | {e['n']} | {e['exact_source']} ({e['exact_source_rate']:.1%}) "
            f"| {e['exact_stress']} ({e['exact_stress_rate']:.1%}) "
            f"| {p['original_plan_is_source']} | {p['original_plan_is_stress']} |"
        )

    lines += [
        "",
        "## Paired McNemar (valid repair)",
        "",
        "| Comparison | n00 | A only | B only | n11 | p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, value in result["mcnemar_valid"].items():
        lines.append(
            f"| {name} | {value['n00']} | {value['a_only']} | {value['b_only']} "
            f"| {value['n11']} | {value['p']:.6g} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    result = audit(_load(args.collection))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result, args.collection))


if __name__ == "__main__":
    main()
