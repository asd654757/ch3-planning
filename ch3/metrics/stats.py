"""Statistical tests for the frozen experiment protocol.

McNemar's test for paired binary outcomes (B0 vs B1, R0 vs R1, R0 vs R2, R1 vs R2).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from ch3.metrics.metrics import load_records


def _mcnemar_exact(b: int, c: int) -> float:
    """Exact McNemar test p-value (binomial). Requires math.comb."""
    from math import comb
    n = b + c
    if n == 0:
        return 1.0
    # Two-sided binomial test
    p = sum(comb(n, k) for k in range(0, min(b, c) + 1)) / (2 ** (n - 1))
    return min(1.0, p)


def paired_binary_outcomes(
    records: list[dict[str, Any]],
    record_type_a: str,
    record_type_b: str,
    filter_a: Optional[dict[str, Any]] = None,
    filter_b: Optional[dict[str, Any]] = None,
    metric: str = "valid",
) -> tuple[int, int, int, int]:
    """Extract paired binary outcomes for McNemar test.

    Returns (n_both_success, n_a_only, n_b_only, n_both_fail).
    """
    # Build lookup: (task_id, seed) -> outcome
    def outcome(rec_type: str, filters: Optional[dict]) -> dict[tuple[str, int], bool]:
        out = {}
        for r in records:
            if r["record_type"] != rec_type:
                continue
            if filters:
                skip = False
                for k, v in filters.items():
                    if r.get(k) != v:
                        skip = True
                        break
                if skip:
                    continue
            key = (r["task_id"], r["seed"])
            if metric == "valid":
                out[key] = r["valid"]
            elif metric == "goal":
                out[key] = bool(r.get("goal_satisfied"))
        return out

    outcomes_a = outcome(record_type_a, filter_a)
    outcomes_b = outcome(record_type_b, filter_b)

    common_keys = set(outcomes_a.keys()) & set(outcomes_b.keys())
    both_success = sum(1 for k in common_keys if outcomes_a[k] and outcomes_b[k])
    a_only = sum(1 for k in common_keys if outcomes_a[k] and not outcomes_b[k])
    b_only = sum(1 for k in common_keys if not outcomes_a[k] and outcomes_b[k])
    both_fail = sum(1 for k in common_keys if not outcomes_a[k] and not outcomes_b[k])

    return both_success, a_only, b_only, both_fail


def run_mcnemar_tests(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Run McNemar tests for key comparisons."""
    results = {}

    # B0 vs B1 (valid)
    n00, n10, n01, n11 = paired_binary_outcomes(records, "direct_b0_plan", "initial_shared_plan", metric="valid")
    results["B0_vs_B1_valid"] = {"n00": n00, "a_only": n10, "b_only": n01, "n11": n11, "p": _mcnemar_exact(n10, n01)}

    # B0 vs B1 (goal)
    n00, n10, n01, n11 = paired_binary_outcomes(records, "direct_b0_plan", "initial_shared_plan", metric="goal")
    results["B0_vs_B1_goal"] = {"n00": n00, "a_only": n10, "b_only": n01, "n11": n11, "p": _mcnemar_exact(n10, n01)}

    # R0 vs R1 (valid)
    n00, n10, n01, n11 = paired_binary_outcomes(records, "repair", "repair", filter_a={"repair_mode": "R0"}, filter_b={"repair_mode": "R1"}, metric="valid")
    results["R0_vs_R1_valid"] = {"n00": n00, "a_only": n10, "b_only": n01, "n11": n11, "p": _mcnemar_exact(n10, n01)}

    # R0 vs R2 (valid)
    n00, n10, n01, n11 = paired_binary_outcomes(records, "repair", "repair", filter_a={"repair_mode": "R0"}, filter_b={"repair_mode": "R2"}, metric="valid")
    results["R0_vs_R2_valid"] = {"n00": n00, "a_only": n10, "b_only": n01, "n11": n11, "p": _mcnemar_exact(n10, n01)}

    # R1 vs R2 (valid)
    n00, n10, n01, n11 = paired_binary_outcomes(records, "repair", "repair", filter_a={"repair_mode": "R1"}, filter_b={"repair_mode": "R2"}, metric="valid")
    results["R1_vs_R2_valid"] = {"n00": n00, "a_only": n10, "b_only": n01, "n11": n11, "p": _mcnemar_exact(n10, n01)}

    return results


def format_stats(results: dict[str, Any]) -> str:
    lines = ["=== McNemar 配对检验 ==="]
    for name, r in results.items():
        p = r["p"]
        sig = "**" if p < 0.05 else "  "
        lines.append(f"  {name:25s}  n00={r['n00']:3d}  a_only={r['a_only']:3d}  b_only={r['b_only']:3d}  n11={r['n11']:3d}  p={p:.4f} {sig}")
    return "\n".join(lines)
