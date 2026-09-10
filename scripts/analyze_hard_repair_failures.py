#!/usr/bin/env python3
"""Analyze hard-task repair failures in a formal collection JSONL.

This is a diagnostic-only script.  It does not modify any experiment records.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
from typing import Any, Mapping, Optional


def _plan(plan: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not plan:
        return []
    return plan.get("actions", []) or []


def _repeat_stats(plan: Optional[Mapping[str, Any]]) -> tuple[int, int, list[str]]:
    """Return (pick_count, unique_pick_count, objects_with_repeated_picks)."""
    picks = [a.get("object_id") for a in _plan(plan) if a.get("skill") == "pick"]
    counter = collections.Counter(picks)
    repeated = [obj for obj, n in counter.items() if n > 1]
    return len(picks), len(counter), repeated


def _bucket(message: str) -> str:
    if "不在 table 上，无法 pick" in message:
        return "pick_object_not_on_table"
    if "不能以 holding 状态结束" in message:
        return "incomplete_pick_no_place"
    if "不在 table 上，无法 place" in message:
        return "place_target_not_on_table"
    if "没有持有任何物体，无法 place" in message:
        return "place_empty_hand"
    if "无法 pick" in message:
        return "other_pick_state"
    if "无法 place" in message:
        return "other_place_state"
    if "不在闭世界场景中" in message:
        return "unknown_object"
    return "other"


def _mcnemar(a: list[bool], b: list[bool]) -> tuple[int, int, int, int, float]:
    n00 = a_only = b_only = n11 = 0
    for x, y in zip(a, b):
        if x and not y:
            a_only += 1
        elif not x and y:
            b_only += 1
        elif not x and not y:
            n00 += 1
        else:
            n11 += 1
    n = a_only + b_only
    k = min(a_only, b_only)
    if n == 0:
        return n00, a_only, b_only, n11, 1.0
    p = 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return n00, a_only, b_only, n11, min(1.0, p)


def load(path: Path) -> tuple[list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    records = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    slices: dict[tuple[str, int], dict[str, Any]] = {}
    for rec in records:
        if rec.get("difficulty") != "hard":
            continue
        key = (rec.get("task_id"), rec.get("seed"))
        sl = slices.setdefault(key, {"repairs": {}})
        typ = rec.get("record_type")
        if typ == "initial_shared_plan":
            sl["b1"] = rec
        elif typ == "direct_b0_plan":
            sl["b0"] = rec
        elif typ == "repair":
            sl["repairs"][rec.get("repair_mode")] = rec
    return records, slices


def analyze(records: list[dict[str, Any]], slices: dict[tuple[str, int], dict[str, Any]]) -> str:
    invalid_b1 = [s["b1"] for s in slices.values() if "b1" in s and not s["b1"].get("valid")]
    lines = [
        "# Hard repair failure analysis",
        "",
        f"- Hard slices: **{len(slices)}**",
        f"- Invalid B1 slices repaired: **{len(invalid_b1)}**",
        f"- Invalid B1 with repeated picks: **{sum(bool(_repeat_stats(r.get("model_plan"))[2]) for r in invalid_b1)}**",
        "",
        "## Initial B1 failure layers",
        "",
    ]
    for (code, layer), n in collections.Counter(
        (r.get("error_code"), r.get("error_layer")) for r in invalid_b1
    ).most_common():
        lines.append(f"- `{code}` / `{layer}`: {n}")

    lines += ["", "## Repair outcome by mode", ""]
    lines.append("| Mode | n | valid | goal | pbw | repeated picks | invalid repeated picks | top failure bucket |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for mode in ("R0", "R1", "R2"):
        rows = [s["repairs"][mode] for s in slices.values() if mode in s["repairs"]]
        if not rows:
            continue
        repeated = sum(bool(_repeat_stats(r.get("model_plan"))[2]) for r in rows)
        invalid = [r for r in rows if not r.get("valid")]
        invalid_repeated = sum(bool(_repeat_stats(r.get("model_plan"))[2]) for r in invalid)
        buckets = collections.Counter(_bucket(r.get("error_message") or "") for r in invalid)
        top_bucket = buckets.most_common(1)[0][0] if buckets else "-"
        lines.append(
            f"| {mode} | {len(rows)} | {sum(bool(r.get('valid')) for r in rows)} "
            f"| {sum(bool(r.get('goal_satisfied')) for r in rows)} "
            f"| {sum(bool(r.get('pass_but_wrong')) for r in rows)} "
            f"| {repeated} | {invalid_repeated} | {top_bucket} |"
        )

    lines += ["", "## Failure buckets by mode", ""]
    for mode in ("R0", "R1", "R2"):
        rows = [s["repairs"][mode] for s in slices.values() if mode in s["repairs"]]
        invalid = [r for r in rows if not r.get("valid")]
        if not invalid:
            continue
        lines.append(f"### {mode}")
        for bucket, n in collections.Counter(_bucket(r.get("error_message") or "") for r in invalid).most_common():
            lines.append(f"- {bucket}: {n}")
        lines.append("")

    lines += ["## Paired valid-plan McNemar on hard repairs", ""]
    lines.append("| Comparison | n00 | a_only | b_only | n11 | p |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for a, b in (("R0", "R1"), ("R0", "R2"), ("R1", "R2")):
        valid_a, valid_b = [], []
        for s in slices.values():
            if a in s["repairs"] and b in s["repairs"]:
                valid_a.append(bool(s["repairs"][a].get("valid")))
                valid_b.append(bool(s["repairs"][b].get("valid")))
        n00, a_only, b_only, n11, p = _mcnemar(valid_a, valid_b)
        lines.append(f"| {a} vs {b} | {n00} | {a_only} | {b_only} | {n11} | {p:.6f} |")

    lines += [
        "",
        "## Key observation",
        "",
        "The dominant hard-task failure is not generic state planning, but repeated selection",
        "of the same object.  The current instruction uses color/shape descriptions while the",
        "closed-world goal contains distinct object IDs, so a model can map several clauses",
        "to the same object.  This produces a later `pick_object_not_on_table` failure after",
        "the object has already been placed.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path)
    parser.add_argument("--output", type=Path, help="Markdown output path")
    args = parser.parse_args()
    records, slices = load(args.collection)
    report = analyze(records, slices)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Wrote {args.output}")
    print(report)


if __name__ == "__main__":
    main()
