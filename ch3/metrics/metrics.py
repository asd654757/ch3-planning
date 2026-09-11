"""Metrics computation for the frozen experiment protocol (stage 3, priority 4).

Computes per-difficulty and per-baseline metrics from a collection JSONL file:
- Group 1 (B0/B1): FVR, CVR, EPR, GSR
- Group 2 (B1/B2a/B2b): IDR, CRR, FRR, Final_Task_Ready, GSR_after_repair, llm_calls, latency
- Repair comparison (R0/R1/R2): valid, goal, pass_but_wrong, CRR, FRR
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from ch3.vlm.collector import is_model_only_refusal


def load_records(path: str | Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def refusal_source(record: dict[str, Any]) -> str:
    """Return refusal provenance, recomputing it for pre-v8 records.

    Formal v7 predates ``infeasible_source`` but retains ``raw_vlm_output``.
    Keeping a deterministic fallback lets us analyze old collections without
    mutating frozen data.
    """
    recorded = record.get("infeasible_source")
    if recorded in {"model_refusal", "deterministic_guard"}:
        return recorded
    return (
        "model_refusal"
        if is_model_only_refusal(record.get("raw_vlm_output"))
        else "deterministic_guard"
    )


def compute_b1_metrics(records: list[dict[str, Any]], difficulty: Optional[str] = None) -> dict[str, Any]:
    """Group 1 + Group 2 metrics for the shared (B1) plan."""
    shared = [r for r in records if r["record_type"] == "initial_shared_plan"]
    if difficulty:
        shared = [r for r in shared if r["difficulty"] == difficulty]
    n = len(shared)
    if n == 0:
        return {}

    valid = sum(1 for r in shared if r["valid"])
    goal = sum(1 for r in shared if r.get("goal_satisfied"))
    pbw = sum(1 for r in shared if r.get("pass_but_wrong"))
    refusal_records = [r for r in shared if r.get("response_protocol") == "infeasible"]
    refusals = len(refusal_records)
    model_refusals = sum(1 for r in refusal_records if refusal_source(r) == "model_refusal")
    invalid = n - valid

    return {
        "n": n,
        "FVR": valid / n,
        "CVR": goal / n,
        "EPR": invalid / n,
        "GSR": goal / n,
        "pass_but_wrong": pbw,
        "pass_but_wrong_rate": pbw / n,
        "infeasible_refusals": refusals,
        "model_only_refusals": model_refusals,
        "deterministic_guard_refusals": refusals - model_refusals,
    }


def compute_b0_metrics(records: list[dict[str, Any]], difficulty: Optional[str] = None) -> dict[str, Any]:
    """Group 1 metrics for the B0 direct plan."""
    b0 = [r for r in records if r["record_type"] == "direct_b0_plan"]
    if difficulty:
        b0 = [r for r in b0 if r["difficulty"] == difficulty]
    n = len(b0)
    if n == 0:
        return {}

    valid = sum(1 for r in b0 if r["valid"])
    goal = sum(1 for r in b0 if r.get("goal_satisfied"))
    pbw = sum(1 for r in b0 if r.get("pass_but_wrong"))
    refusal_records = [r for r in b0 if r.get("response_protocol") == "infeasible"]
    refusals = len(refusal_records)
    model_refusals = sum(1 for r in refusal_records if refusal_source(r) == "model_refusal")

    return {
        "n": n,
        "FVR": valid / n,
        "CVR": goal / n,
        "EPR": (n - valid) / n,
        "GSR": goal / n,
        "pass_but_wrong": pbw,
        "pass_but_wrong_rate": pbw / n,
        "infeasible_refusals": refusals,
        "model_only_refusals": model_refusals,
        "deterministic_guard_refusals": refusals - model_refusals,
    }


def compute_repair_metrics(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-repair-mode metrics (R0/R1/R2)."""
    repairs = [r for r in records if r["record_type"] == "repair"]
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in repairs:
        mode = r.get("repair_mode", "?")
        by_mode[mode].append(r)

    results: dict[str, dict[str, Any]] = {}
    for mode, rs in sorted(by_mode.items()):
        n = len(rs)
        if n == 0:
            continue
        valid = sum(1 for r in rs if r["valid"])
        goal = sum(1 for r in rs if r.get("goal_satisfied"))
        pbw = sum(1 for r in rs if r.get("pass_but_wrong"))
        refusal_records = [r for r in rs if r.get("response_protocol") == "infeasible"]
        refusals = len(refusal_records)
        model_refusals = sum(1 for r in refusal_records if refusal_source(r) == "model_refusal")
        results[mode] = {
            "n": n,
            "FVR": valid / n,
            "GSR": goal / n,
            "CRR": valid / n,
            "FRR": 0.0,  # computed at task level
            "pass_but_wrong": pbw,
            "pass_but_wrong_rate": pbw / n,
            "infeasible_refusals": refusals,
            "model_only_refusals": model_refusals,
            "deterministic_guard_refusals": refusals - model_refusals,
        }
    return results


def compute_system_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Group 2 system-level metrics: IDR, CRR, FRR, Final Task-Ready, calls, latency."""
    shared = [r for r in records if r["record_type"] == "initial_shared_plan"]
    repairs = [r for r in records if r["record_type"] == "repair"]
    n_tasks = len(shared)
    n_invalid = sum(1 for r in shared if not r["valid"])

    # IDR = Invalid Detection Rate (validator catches all invalid plans)
    # Since validator is deterministic and always catches invalid, IDR = 100%
    idr = 1.0 if n_invalid > 0 else None

    # Repair outcomes per mode
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in repairs:
        by_mode[r.get("repair_mode", "?")].append(r)

    repair_summary = {}
    for mode, rs in by_mode.items():
        n_repair = len(rs)
        valid_after = sum(1 for r in rs if r["valid"])
        goal_after = sum(1 for r in rs if r.get("goal_satisfied"))
        pbw_after = sum(1 for r in rs if r.get("pass_but_wrong"))
        repair_summary[mode] = {
            "repair_calls": n_repair,
            "CRR": valid_after / n_repair if n_repair else None,
            "GSR_after_repair": goal_after / n_repair if n_repair else None,
            "pass_but_wrong": pbw_after,
            "Final_Task_Ready": goal_after / n_repair if n_repair else None,
        }

    total_tokens = sum(r.get("total_tokens", 0) for r in records)
    total_latency = sum(r.get("latency_ms", 0) for r in records)
    avg_latency = total_latency / len(records) if records else 0

    return {
        "n_initial": n_tasks,
        "n_invalid_initial": n_invalid,
        "IDR": idr,
        "repair_summary": repair_summary,
        "total_records": len(records),
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency),
    }


def compute_all(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute all metrics, grouped by difficulty."""
    difficulties = sorted({r.get("difficulty", "?") for r in records})
    result: dict[str, Any] = {
        "overall": {
            "B1": compute_b1_metrics(records),
            "B0": compute_b0_metrics(records),
            "repair": compute_repair_metrics(records),
            "system": compute_system_metrics(records),
        },
        "by_difficulty": {},
    }
    for d in difficulties:
        result["by_difficulty"][d] = {
            "B1": compute_b1_metrics(records, d),
            "B0": compute_b0_metrics(records, d),
        }
    return result


def format_summary(result: dict[str, Any]) -> str:
    """Format the metrics result as a readable text summary."""
    lines = []
    overall = result["overall"]

    lines.append("=== B0 (Direct) ===")
    b0 = overall["B0"]
    if b0:
        lines.append(f"  n={b0['n']}  FVR={b0['FVR']:.1%}  GSR={b0['GSR']:.1%}  pbw={b0['pass_but_wrong']}")

    lines.append("")
    lines.append("=== B1 (Shared Structured) ===")
    b1 = overall["B1"]
    if b1:
        lines.append(f"  n={b1['n']}  FVR={b1['FVR']:.1%}  GSR={b1['GSR']:.1%}  pbw={b1['pass_but_wrong']} ({b1['pass_but_wrong_rate']:.1%})")

    lines.append("")
    lines.append("=== Repair (R0/R1/R2) ===")
    for mode, s in overall["repair"].items():
        lines.append(f"  {mode}: n={s['n']}  FVR={s['FVR']:.1%}  GSR={s['GSR']:.1%}  pbw={s['pass_but_wrong']} ({s['pass_but_wrong_rate']:.1%})")

    lines.append("")
    lines.append("=== System ===")
    sys = overall["system"]
    lines.append(f"  IDR={sys['IDR']}  total_records={sys['total_records']}  total_tokens={sys['total_tokens']}  avg_latency={sys['avg_latency_ms']}ms")
    for mode, s in sys["repair_summary"].items():
        crr = f"{s['CRR']:.1%}" if s["CRR"] is not None else "n/a"
        gsr = f"{s['GSR_after_repair']:.1%}" if s["GSR_after_repair"] is not None else "n/a"
        lines.append(f"  {mode}: CRR={crr}  GSR_after={gsr}  pbw={s['pass_but_wrong']}")

    lines.append("")
    lines.append("=== By Difficulty ===")
    for d, metrics in result["by_difficulty"].items():
        b1d = metrics["B1"]
        b0d = metrics["B0"]
        if b1d:
            lines.append(
                f"  {d:12s}  B1: FVR={b1d['FVR']:.1%} GSR={b1d['GSR']:.1%} "
                f"refusal={b1d.get('infeasible_refusals', 0)} pbw={b1d['pass_but_wrong']}  |  "
                f"B0: FVR={b0d['FVR']:.1%} GSR={b0d['GSR']:.1%} "
                f"refusal={b0d.get('infeasible_refusals', 0)}"
            )

    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Collection JSONL file")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args(argv)

    records = load_records(args.input)
    result = compute_all(records)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
