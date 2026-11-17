"""Frozen-workload pilot: exact, proposal-only task relaxation from failure cores."""
import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_repair import METHODS, SupportConstraints, candidates, editable_ids, propose
from scripts.analyze_fragment_execution import decode
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write


ARMS = (*METHODS, "transfer_support_fixed")
PROTOCOL = Path("docs/fragment_repair_pilot_protocol_20261117.md")
SOURCE = ROOT / "fragment_compact_source_20261116_v1"
TARGET = ROOT / "fragment_execution_validation_20261116_v1"


def cases():
    result = []
    for row in read(TARGET / "fixed_trace.json"):
        for index, step in enumerate(row["decisions"]):
            if step["decision"]["status"] != "ready":
                result.append({"id": f"{row['episode']}:{row['revision']}:{index}",
                    "stage": "task_revision" if index == 0 else "post_action_recovery",
                    "snapshot": step["snapshot"]})
    return result


def signature(result):
    return result.status, result.deleted, result.cost


def validate_plan(d, s, task, plan):
    state = s.state
    for i, event in enumerate(plan, 1):
        assert event.seq == s.cursor + i
        state = next(ns for ns, e in d.model.successors(state, event.seq)
            if (e.kind, e.object, e.target) == (event.kind, event.object, event.target))
    assert len(plan) <= 8
    assert all(d.model.monitor(task, state, (*h, *plan)) == (frozenset(), frozenset())
               for h in d.model.possible_histories(s.history))


def reference(raw_cases):
    """Independent unpruned planner, no certificate or independent lower bound."""
    d = build_compact_domain()
    out, calls = {}, 0
    for raw in raw_cases:
        s = decode(d, raw["snapshot"])
        editable = editable_ids(s)
        rows = candidates(d, s, editable, {i: 1 for i in editable})
        answer = ("no_authorized_repair", (), None)
        for cost, ids, task in rows:
            calls += 1
            decision = d.model.decide(s, task, 8)
            if decision.status == "ready":
                validate_plan(d, s, task, decision.plan)
                answer = ("proposal", ids, cost)
                break
        out[raw["id"]] = answer
    return out, calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for folder in (SOURCE, TARGET):
        check(folder)
    args.output.mkdir(parents=True, exist_ok=False)
    files = [PROTOCOL, Path(__file__).resolve().relative_to(Path.cwd()),
        Path("scripts/analyze_fragment_execution.py"), Path("scripts/benchmark_fragment_three_objects.py"),
        Path("tests/test_fragment_repair.py"), *sorted(Path("ch3/revision").glob("*.py"))]
    hashes = {str(p): digest(p) for p in files}
    for path in files:
        dest = args.output / "sources" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    raw_cases = cases()
    write(args.output / "plan.json", {"arms": ARMS, "repeats": 3, "horizon": 8,
        "cases": len(raw_cases), "case_selection": "all_nonready_in_frozen_trace",
        "source": str(SOURCE), "target": str(TARGET), "protocol_sha256": digest(PROTOCOL),
        "model_calls": 0, "physical_execution": False})
    print(json.dumps({"phase": "frozen", "cases": len(raw_cases), "protocol": digest(PROTOCOL)}), flush=True)
    gold, calls = reference(raw_cases)
    write(args.output / "reference.json", {k: list(v) for k, v in gold.items()})
    print(json.dumps({"phase": "reference", "calls": calls}), flush=True)
    timings, first = [], {}
    for repeat in range(3):
        order = ARMS[repeat * 2:] + ARMS[:repeat * 2]
        if repeat == 1:
            order = tuple(reversed(order))
        for arm in order:
            started = perf_counter()
            d = build_compact_domain()
            lower = IndependentActionBound(d)
            certificates = read(SOURCE / ("fixed_certificates.json" if arm.endswith("_fixed")
                                        else "learned_certificates.json")) if arm.startswith("transfer") else []
            bank = d.regions.RegionBank(certificates)
            decoded = [(r, decode(d, r["snapshot"])) for r in raw_cases]
            prepared = perf_counter()
            records = []
            for raw, s in decoded:
                result = propose(d, s, bank, lower, arm.removesuffix("_fixed"), known_nonready=True)
                records.append({"id": raw["id"], "stage": raw["stage"], "result": result})
            finished = perf_counter()
            # All validation is outside the measurement interval.
            aggregate = Counter()
            for (raw, s), record in zip(decoded, records):
                result = record["result"]
                assert signature(result) == gold[raw["id"]]
                aggregate.update(result.stats)
                if result.status == "proposal":
                    assert result.contract.version == s.contract.version + 1
                    assert result.contract.obligations == tuple(o for o in s.contract.obligations
                        if o.clause.id not in result.deleted)
                    assert set(result.deleted) <= set(editable_ids(s))
                    validate_plan(d, s, result.contract, result.plan)
            row = {"repeat": repeat, "arm": arm, "prep_ms": (prepared-started)*1000,
                "repair_ms": (finished-prepared)*1000, "total_ms": (finished-started)*1000,
                "stats": dict(aggregate)}
            timings.append(row)
            if repeat == 0:
                first[arm] = records
                write(args.output / f"{arm}_proposals.json", records)
            print(json.dumps(row), flush=True)
    # Independent equivalence check over EVERY authorized deletion subset,
    # including those after the optimum which the search never visited.
    d = build_compact_domain()
    bank = d.regions.RegionBank(read(SOURCE / "learned_certificates.json"))
    support_checks = 0
    for raw in raw_cases:
        s = decode(d, raw["snapshot"])
        supported = SupportConstraints(d, bank, s)
        editable = editable_ids(s)
        for _, ids, task in candidates(d, s, editable, {i: 1 for i in editable}):
            assert supported.blocked(ids) == (bank.evidence(replace(s, contract=task)) is not None)
            support_checks += 1
    methods = {}
    for arm in ARMS:
        rows = [r for r in timings if r["arm"] == arm]
        methods[arm] = {"medians": {key: median(r[key] for r in rows)
            for key in ("prep_ms", "repair_ms", "total_ms")}, "stats": rows[0]["stats"]}
        assert all(r["stats"] == rows[0]["stats"] for r in rows)
    counts = Counter(v[0] for v in gold.values())
    stages = {}
    for stage in ("task_revision", "post_action_recovery"):
        ids = [r["id"] for r in raw_cases if r["stage"] == stage]
        stages[stage] = {"cases": len(ids), "proposals": sum(gold[i][0] == "proposal" for i in ids)}
    # A diagnostic of the precise support formula's marginal pruning benefit.
    effects = []
    for i, raw in enumerate(raw_cases):
        by_arm = {arm: first[arm][i]["result"] for arm in ARMS}
        if by_arm["transfer_union"].stats["backend_calls"] != by_arm["transfer_support"].stats["backend_calls"]:
            effects.append({"id": raw["id"], "stage": raw["stage"], "snapshot": raw["snapshot"],
                "backend_calls": {arm: value.stats["backend_calls"] for arm, value in by_arm.items()},
                "deleted": by_arm["transfer_support"].deleted})
    summary = {"cases": len(raw_cases), "outcomes": dict(counts), "by_stage": stages,
        "unpruned_reference_calls": calls, "all_answers_optimal_and_identical": True,
        "all_retained_obligations_and_history_anchors_unchanged": True,
        "support_formula_equals_candidate_recheck_at_subsets": support_checks,
        "support_vs_union_cases_with_different_backend_calls": len(effects),
        "methods": methods, "model_calls": 0, "physical_execution": False,
        "source_learning_cost_included": False}
    write(args.output / "support_effects.json", effects)
    write(args.output / "timings.json", timings)
    write(args.output / "summary.json", summary)
    close(args.output, hashes, {str(f / "manifest.json"): digest(f / "manifest.json") for f in (SOURCE, TARGET)})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
