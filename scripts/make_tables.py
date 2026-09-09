"""Generate experiment tables (CSV + Markdown) from a collection JSONL.

Usage:
    python scripts/make_tables.py data/collections/formal_v1_*.jsonl --output-dir results/
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ch3.metrics.metrics import compute_all, load_records
from ch3.metrics.stats import format_stats, run_mcnemar_tests


def make_table1(records: list[dict]) -> tuple[list[list], list[str]]:
    """Table 1: Experiment A — B0 vs B1 by difficulty."""
    difficulties = ["easy", "medium", "hard", "infeasible"]
    headers = ["Difficulty", "B0_n", "B0_FVR", "B0_GSR", "B1_n", "B1_FVR", "B1_GSR", "B1_pbw"]
    rows = []
    for d in difficulties:
        b0 = compute_b0_for(records, d)
        b1 = compute_b1_for(records, d)
        rows.append([
            d,
            b0.get("n", 0), f"{b0.get('FVR', 0):.1%}", f"{b0.get('GSR', 0):.1%}",
            b1.get("n", 0), f"{b1.get('FVR', 0):.1%}", f"{b1.get('GSR', 0):.1%}", b1.get("pass_but_wrong", 0),
        ])
    return rows, headers


def compute_b0_for(records: list[dict], difficulty: str) -> dict:
    from ch3.metrics.metrics import compute_b0_metrics
    return compute_b0_metrics(records, difficulty)


def compute_b1_for(records: list[dict], difficulty: str) -> dict:
    from ch3.metrics.metrics import compute_b1_metrics
    return compute_b1_metrics(records, difficulty)


def make_table2(records: list[dict]) -> tuple[list[list], list[str]]:
    """Table 2: Repair comparison — R0 vs R1 vs R2."""
    from ch3.metrics.metrics import compute_repair_metrics
    repair = compute_repair_metrics(records)
    headers = ["Mode", "n", "FVR", "GSR", "pass_but_wrong"]
    rows = []
    for mode in ["R0", "R1", "R2"]:
        s = repair.get(mode, {})
        if s:
            rows.append([mode, s["n"], f"{s['FVR']:.1%}", f"{s['GSR']:.1%}", s["pass_but_wrong"]])
    return rows, headers


def make_table3(records: list[dict]) -> tuple[list[list], list[str]]:
    """Table 3: McNemar tests."""
    stats = run_mcnemar_tests(records)
    headers = ["Comparison", "n00", "a_only", "b_only", "n11", "p_value", "significant"]
    rows = []
    for name, r in stats.items():
        rows.append([name, r["n00"], r["a_only"], r["b_only"], r["n11"], f"{r['p']:.4f}", "yes" if r["p"] < 0.05 else "no"])
    return rows, headers


def write_csv(rows: list[list], headers: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def write_md(rows: list[list], headers: list[str], path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("|" + "|".join(["---"] * len(headers)) + "|\n")
        for row in rows:
            f.write("| " + " | ".join(str(c) for c in row) + " |\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Collection JSONL file")
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args(argv)

    records = load_records(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Table 1
    rows, headers = make_table1(records)
    write_csv(rows, headers, out_dir / "table1_b0_vs_b1.csv")
    write_md(rows, headers, out_dir / "table1_b0_vs_b1.md", "Table 1: B0 (Direct) vs B1 (Shared Structured)")
    print(f"Table 1 → {out_dir / 'table1_b0_vs_b1.md'}")

    # Table 2
    rows, headers = make_table2(records)
    write_csv(rows, headers, out_dir / "table2_repair.csv")
    write_md(rows, headers, out_dir / "table2_repair.md", "Table 2: Repair Comparison (R0/R1/R2)")
    print(f"Table 2 → {out_dir / 'table2_repair.md'}")

    # Table 3
    rows, headers = make_table3(records)
    write_csv(rows, headers, out_dir / "table3_mcnemar.csv")
    write_md(rows, headers, out_dir / "table3_mcnemar.md", "Table 3: McNemar Paired Tests")
    print(f"Table 3 → {out_dir / 'table3_mcnemar.md'}")

    # Print summary
    print()
    result = compute_all(records)
    from ch3.metrics.metrics import format_summary
    print(format_summary(result))
    print()
    stats = run_mcnemar_tests(records)
    print(format_stats(stats))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
