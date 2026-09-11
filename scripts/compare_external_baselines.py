#!/usr/bin/env python3
"""Generate paired comparison tables for external repair-pressure baselines."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pressure_rows(records: list[dict[str, Any]]) -> dict[str, dict[tuple[str, int, str], dict[str, Any]]]:
    result: dict[str, dict[tuple[str, int, str], dict[str, Any]]] = defaultdict(dict)
    for row in records:
        if row.get("record_type") != "repair_pressure":
            continue
        key = (str(row["task_id"]), int(row["seed"]), str(row["pressure_type"]))
        result[str(row["repair_mode"])][key] = row
    return result


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact McNemar/binomial tail probability."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1))
    return min(1.0, 2 * tail / 2**n)


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    valid = sum(bool(r.get("valid")) for r in rows)
    goal = sum(bool(r.get("goal_satisfied")) for r in rows)
    return {
        "n": n,
        "valid": valid,
        "CRR": valid / n,
        "goal": goal,
        "GSR_after": goal / n,
        "FRR": sum(bool(r.get("valid")) and not bool(r.get("goal_satisfied")) for r in rows) / n,
        "pass_but_wrong": sum(bool(r.get("pass_but_wrong")) for r in rows),
        "total_tokens": sum(
            int(r.get("total_tokens", 0) or 0) + int(r.get("baseline_total_tokens", 0) or 0)
            - int(r.get("total_tokens", 0) or 0)
            for r in rows
        ),
        "avg_rounds": sum(int(r.get("baseline_rounds", 1) or 1) for r in rows) / n,
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-collection", required=True)
    parser.add_argument("--external-collection", required=True)
    parser.add_argument("--output-prefix", required=True)
    args = parser.parse_args(argv)

    formal = pressure_rows(load_jsonl(args.formal_collection))
    external = pressure_rows(load_jsonl(args.external_collection))
    keys = sorted(set().union(*(d.keys() for d in formal.values()), *(d.keys() for d in external.values())))
    if not keys:
        raise ValueError("No paired repair-pressure cases found")

    modes = ["R0", "R1", "R2", "self_refine", "checker_loop"]
    summary_rows = []
    for mode in modes:
        d = formal.get(mode) or external.get(mode)
        if not d:
            continue
        s = summary(list(d.values()))
        summary_rows.append({"method": mode, **s})
    prefix = Path(args.output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    summary_path = prefix.with_name(prefix.name + "_overall.csv")
    write_csv(
        summary_path,
        ["method", "n", "valid", "CRR", "goal", "GSR_after", "FRR", "pass_but_wrong", "total_tokens", "avg_rounds"],
        summary_rows,
    )

    by_pressure_rows = []
    for pressure_type in sorted({k[2] for k in keys}):
        for mode in modes:
            d = formal.get(mode) or external.get(mode)
            if not d:
                continue
            rows = [d[k] for k in keys if k in d and k[2] == pressure_type]
            s = summary(rows)
            by_pressure_rows.append({"pressure_type": pressure_type, "method": mode, **s})
    by_pressure_path = prefix.with_name(prefix.name + "_by_pressure.csv")
    write_csv(
        by_pressure_path,
        ["pressure_type", "method", "n", "valid", "CRR", "goal", "GSR_after", "FRR", "pass_but_wrong", "total_tokens", "avg_rounds"],
        by_pressure_rows,
    )

    paired_rows = []
    for ext_mode in ["self_refine", "checker_loop"]:
        ext_data = external.get(ext_mode)
        if not ext_data:
            continue
        for formal_mode in ["R0", "R1", "R2"]:
            formal_data = formal.get(formal_mode)
            if not formal_data:
                continue
            usable = [k for k in keys if k in ext_data and k in formal_data]
            only_ext = sum(bool(ext_data[k].get("valid")) and not bool(formal_data[k].get("valid")) for k in usable)
            only_formal = sum(not bool(ext_data[k].get("valid")) and bool(formal_data[k].get("valid")) for k in usable)
            paired_rows.append(
                {
                    "method_a": ext_mode,
                    "method_b": formal_mode,
                    "n_pairs": len(usable),
                    "success_a": sum(bool(ext_data[k].get("valid")) for k in usable),
                    "success_b": sum(bool(formal_data[k].get("valid")) for k in usable),
                    "both_success": sum(bool(ext_data[k].get("valid")) and bool(formal_data[k].get("valid")) for k in usable),
                    "only_a": only_ext,
                    "only_b": only_formal,
                    "mcnemar_exact_p": exact_mcnemar(only_ext, only_formal),
                }
            )
    paired_path = prefix.with_name(prefix.name + "_mcnemar.csv")
    write_csv(
        paired_path,
        ["method_a", "method_b", "n_pairs", "success_a", "success_b", "both_success", "only_a", "only_b", "mcnemar_exact_p"],
        paired_rows,
    )
    print(json.dumps({"overall": str(summary_path), "by_pressure": str(by_pressure_path), "mcnemar": str(paired_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
