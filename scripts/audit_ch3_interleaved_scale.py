"""Recheck all history-scale cells in interleaved, position-balanced rounds."""

import argparse
from collections import Counter, deque
import json
import os
from pathlib import Path
import platform
from statistics import median
from types import SimpleNamespace

from ch3.revision.fragment_domain_adapter import _function, build_domain
from scripts.benchmark_ch3_history_scale import ARMS, HORIZON, OBJECTS, bind, run_arm, workload


SCALE = ((3, 1), (3, 2), (3, 3), (3, 4), (4, 1), (4, 2), (4, 3), (4, 4))
POSITIONS = (0, 1, 7, 2, 6, 3, 5, 4)


def signature(rows):
    return [{k: v for k, v in row.items() if k != "elapsed_ns"} for row in rows]


def search_nodes(model, validate, cases, arm, expected):
    """Count deque removals in a separate run, outside latency measurement."""
    by_family = Counter()
    pops = 0

    class CountingDeque(deque):
        def popleft(self):
            nonlocal pops
            pops += 1
            return super().popleft()

    search = _function(model._search, {**model._search.__globals__, "deque": CountingDeque})
    instrumented = SimpleNamespace(**{**vars(model), "_search": search})
    for case, expected_row in zip(cases, expected):
        pops = 0
        rows, _ = run_arm(instrumented, validate, arm, [case])
        assert signature(rows) == [expected_row]
        by_family[case[1]] += pops
    return dict(by_family)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu", type=int)
    args = parser.parse_args()
    if args.cpu is not None:
        os.sched_setaffinity(0, {args.cpu})
    archive = json.loads(args.reference.read_text())
    references = {(r["objects"], r["unknown"]): r for r in archive["cells"]}
    prepared = {}
    for n in (3, 4):
        model = build_domain(OBJECTS[:n]).model
        validate, _ = bind(model)
        for k in (1, 2, 3, 4):
            cases = list(workload(model, k))
            assert [case[0] for case in cases] == [row["case"] for row in references[n, k]["cases"]]
            prepared[n, k] = (model, validate, cases)
    samples = {cell: [] for cell in SCALE}
    orders = []
    for repeat in range(len(SCALE)):
        order = [SCALE[(offset + repeat) % len(SCALE)] for offset in POSITIONS]
        orders.append(order)
        for cell in order:
            model, validate, cases = prepared[cell]
            arms = ARMS if repeat < len(ARMS) else ARMS[::-1]
            shift = (repeat + SCALE.index(cell)) % len(arms)
            for arm in (*arms[shift:], *arms[:shift]):
                rows, timing = run_arm(model, validate, arm, cases)
                assert signature(rows) == references[cell]["decisions"][arm]
                samples[cell].append({"arm": arm, "repeat": repeat, **timing})
        print(f"Completed interleaved round {repeat + 1}/{len(SCALE)}", flush=True)
    results = []
    for cell, (model, validate, cases) in prepared.items():
        per_arm = {}
        for arm in ARMS:
            matching = [row for row in samples[cell] if row["arm"] == arm]
            expected = references[cell]["decisions"][arm]
            per_arm[arm] = {
                "correct": sum(row["status"] == row["expected"] and row["valid"] for row in expected),
                "decision_ms": median(row["decision_ms"] for row in matching),
                "family_ms": {family: median(row["family_ms"][family] for row in matching)
                              for family in matching[0]["family_ms"]},
                "search_deque_pops_by_family": search_nodes(model, validate, cases, arm, expected),
            }
        results.append({"objects": cell[0], "unknown": cell[1], "results": per_arm, "timings": samples[cell]})
        print(cell, {arm: round(r["decision_ms"], 3) for arm, r in per_arm.items()}, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump({"round_orders": orders, "cells": results, "reference": str(args.reference),
                   "cpu": args.cpu, "python": platform.python_version(), "horizon": HORIZON},
                  stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
