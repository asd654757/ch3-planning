#!/usr/bin/env python3
"""Collect the P1 determinism-fix and guard-ablation evidence into one JSON.

Four pairwise comparisons of ``symbolic_planner_cases.jsonl`` files:
pre-fix vs deterministic (both protocols) and guard-on vs guard-off (both
protocols).  Read-only over the artifacts; the guard-off run lives in
``data/reports/ablation/planner_guard_off.py``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FIELDS = (
    "suffix_actions",
    "search_success",
    "search_reason",
    "symbolic_valid",
    "goal_satisfied",
    "sim_success",
    "final_success",
    "single_draw_success",
    "observed_state_facts",
    "goal_holds_in_observed_state",
)
DIFF = "data/reports/ablation/diff_cases.py"

COMPARISONS = {
    "hash_seed_1_vs_7_formal428": (
        "data/reports/ablation/formal428_hseed1/symbolic_planner_cases.jsonl",
        "data/reports/ablation/formal428_hseed7/symbolic_planner_cases.jsonl",
    ),
    "pre_fix_vs_deterministic_formal428": (
        "data/reports/symbolic_planner_formal428_20260926_045106/symbolic_planner_cases.jsonl",
        "data/reports/symbolic_planner_formal428_20260926_det163525/symbolic_planner_cases.jsonl",
    ),
    "pre_fix_vs_deterministic_fair100": (
        "data/reports/symbolic_planner_fair100_20260926_045545/symbolic_planner_cases.jsonl",
        "data/reports/symbolic_planner_fair100_20260926_det1640/symbolic_planner_cases.jsonl",
    ),
    "guard_on_vs_off_formal428": (
        "data/reports/ablation/formal428_guardon/symbolic_planner_cases.jsonl",
        "data/reports/ablation/formal428_guardoff/symbolic_planner_cases.jsonl",
    ),
    "guard_on_vs_off_fair100": (
        "data/reports/ablation/fair100_guardon/symbolic_planner_cases.jsonl",
        "data/reports/ablation/fair100_guardoff/symbolic_planner_cases.jsonl",
    ),
}


def diff(a: str, b: str) -> dict:
    command = [sys.executable, DIFF, "--a", a, "--b", b]
    for field in FIELDS:
        command += ["--field", field]
    proc = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    return {
        "a": a,
        "b": b,
        "keys_shared": payload["keys_shared"],
        "only_in_a": payload["only_in_a"],
        "only_in_b": payload["only_in_b"],
        "identical_all_fields": payload["identical_all_fields"],
        "differ_by_field": {
            field: cell["count"] for field, cell in payload["differ_by_field"].items()
        },
        "differ_by_mode": payload["differ_by_mode"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = {
        "record_type": "p1_determinism_evidence",
        "comparisons": {label: diff(a, b) for label, (a, b) in COMPARISONS.items()},
        "plan_identity_fair100": json.loads(
            Path("data/reports/plan_identity_fair100_det1640.json").read_text(encoding="utf-8")
        ),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["comparisons"], ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
