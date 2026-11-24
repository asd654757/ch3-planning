"""Cross unknown-event count with registered object count on a fixed case mix."""

import argparse
from dataclasses import asdict, replace
from itertools import permutations
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter_ns
from types import SimpleNamespace

from ch3.revision.fragment_domain_adapter import _function, build_domain
from scripts.compare_ch3_history_monitors import HORIZON, bind, counts, decision_functions, obligations, visit
from scripts.profile_ch3_failure_lookup import PhaseClock


ARMS = ("branch_candidate_validation", "same_history_replan", "routed")
FAMILIES = ("inspection_order", "protected_object", "confirmed_backup", "repeat_inspection")
OBJECTS = ("blue", "yellow", "red", "white")
REPEATS = 8


def workload(m, unknown_count):
    # The known alternatives occupy the same ledger slots across k. Roles and
    # four scenario weights are identical across all scale combinations.
    for item, cargo, anchor in permutations(OBJECTS[:3]):
        for family in FAMILIES:
            checkpoints = [(item, "green"), (item, "table"), (cargo, "green"), (cargo, "return")]
            final = dict(id="final", kind="at", object=cargo, target="return")
            prior_obligations = [final]
            if len(m.OBJECTS) == 4:
                prior_obligations.append(dict(id="untouched", kind="forbid", object="white"))
            state = m.State()
            status = "ready"
            extra = []
            tail = ()
            inactive = "confirmed"
            if family == "inspection_order":
                state = m.State(**{anchor: "return"})
                final.update(target="green")
                tail = (m.Event(5, "place", anchor, "return", observation_id="shipment"),)
                extra = [visit("ship", anchor, "return")]
                for index, (obj, location) in enumerate(checkpoints):
                    check = f"required-{index}"
                    extra += [visit(check, obj, location),
                              dict(id=f"order-{index}", kind="before", first=check, second="ship")]
                status = "need_observation"
            elif family == "protected_object":
                checkpoints = [(item, target) for target in ("green", "table", "return", "table")]
                extra = [dict(id="protected", kind="forbid", object=item)]
                inactive = "refuted"
                status = "need_observation"
            else:
                extra = [visit(f"required-{i}", obj, location) for i, (obj, location) in enumerate(checkpoints)]
                if family == "confirmed_backup":
                    tail = tuple(m.Event(i+5, "place", obj, location, observation_id=f"backup-{i}")
                                 for i, (obj, location) in enumerate(checkpoints))
            events = tuple(m.Event(i+1, "place", obj, location, "unknown" if i < unknown_count else inactive,
                                   f"inspection-{i}") for i, (obj, location) in enumerate(checkpoints)) + tail
            old_task = m.Contract(0, obligations(m, *prior_obligations))
            source = m.Snapshot(old_task, state, events, "stable-observation")
            plan = m.decide(source, old_task, HORIZON)
            assert plan.status == "ready" and len(plan.plan) == 2
            task = m.Contract(1, obligations(m, *prior_obligations, *extra))
            snapshot = replace(source, contract=task).validate()
            yield f"{family}/{item}-{cargo}-{anchor}", family, snapshot, plan.plan, status


def run_arm(m, validate, arm, cases):
    clock = PhaseClock()
    timed_search = clock.measure("internal_search", m._search)

    def search(contract, state, histories, cursor, horizon):
        clock.calls["history_inputs"] += len(histories)
        return timed_search(contract, state, histories, cursor, horizon)

    outer = _function(m.decide, {**m.decide.__globals__, "_search": search})
    view = SimpleNamespace(**{**vars(m), "decide": outer})
    decide = decision_functions(view, validate, clock)[arm]
    rows = []
    for case, family, snap, old, gold in cases:
        start = perf_counter_ns()
        status, plan = decide(snap, old)
        elapsed = perf_counter_ns() - start
        rows.append({"case": case, "family": family, "stage": "unknown", "expected": gold,
                     "status": status, "valid": status != "ready" or validate(snap, snap.contract, plan),
                     "elapsed_ns": elapsed})
    return rows, {
        "decision_ms": sum(r["elapsed_ns"] for r in rows) / 1e6,
        "family_ms": {f: sum(r["elapsed_ns"] for r in rows if r["family"] == f) / 1e6 for f in FAMILIES},
        **dict(clock.calls),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cells = []
    for n in (3, 4):
        m = build_domain(OBJECTS[:n]).model
        validate, _ = bind(m)
        for k in (1, 2, 3, 4):
            cases = list(workload(m, k))
            assert len(cases) == 24
            for case, _, snap, _, expected in cases:
                assert sum(e.evidence == "unknown" for e in snap.history) == k
                assert m.decide(snap, snap.contract, HORIZON).status == expected, case
            observations, samples = {}, []
            for repeat in range(REPEATS):
                order = ARMS if repeat % 2 == 0 else ARMS[::-1]
                shift = (repeat // 2) % len(order)
                for arm in (*order[shift:], *order[:shift]):
                    rows, timing = run_arm(m, validate, arm, cases)
                    signature = [{key: value for key, value in row.items() if key != "elapsed_ns"} for row in rows]
                    assert observations.setdefault(arm, signature) == signature
                    samples.append({"arm": arm, "repeat": repeat, **timing})
            results = {}
            for arm, rows in observations.items():
                matched = [s for s in samples if s["arm"] == arm]
                results[arm] = {**counts(rows), **{key: median(sample.get(key, 0) for sample in matched)
                    for key in ("decision_ms", "solver", "internal_search", "history_inputs", "suffix_validation")},
                    "family_ms": {f: median(s["family_ms"][f] for s in matched) for f in FAMILIES}}
            assert results["routed"]["correct"] == len(cases)
            assert results["same_history_replan"]["correct"] == len(cases)
            entry = {"objects": n, "unknown": k, "histories": 2**k, "results": results, "timings": samples,
                     "decisions": observations, "cases": [{"case": case, "family": family,
                        "snapshot": asdict(snapshot), "prior_plan": [asdict(e) for e in old], "expected": expected}
                        for case, family, snapshot, old, expected in cases]}
            cells.append(entry)
            print(n, k, {arm: (r["correct"], r["solver"], r["internal_search"], round(r["decision_ms"], 2))
                         for arm, r in results.items()}, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump({"python": platform.python_version(), "platform": platform.platform(), "horizon": HORIZON,
                   "repeats": REPEATS, "cells": cells}, stream, default=lambda obj: obj.model_dump(), indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
