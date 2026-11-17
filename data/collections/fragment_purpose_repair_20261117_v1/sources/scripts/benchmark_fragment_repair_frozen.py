"""Follow-up pilot: frozen certificates, no new online conflict extraction."""
import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_repair import SupportConstraints, candidates, editable_ids
from ch3.revision.fragment_repair_frozen import METHODS, propose_frozen
from scripts.analyze_fragment_execution import decode
from scripts.benchmark_fragment_repair import SOURCE, TARGET, cases, reference, signature, validate_plan
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write

PROTOCOL = Path("docs/fragment_repair_frozen_protocol_20261117.md")
FIXTURES = ROOT / "fragment_three_fixtures_20261116_v2"
STATIC = ROOT / "fragment_three_validation_20261116_v1"
PILOT = ROOT / "fragment_repair_pilot_20261117_v1"
ARMS = (*METHODS, "transfer_support_fixed", "transfer_recheck_fixed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = (SOURCE, TARGET, FIXTURES, STATIC, PILOT)
    for folder in inputs:
        check(folder)
    args.output.mkdir(parents=True, exist_ok=False)
    files = [PROTOCOL, Path(__file__).resolve().relative_to(Path.cwd()),
        Path("scripts/benchmark_fragment_repair.py"), Path("scripts/analyze_fragment_execution.py"),
        Path("scripts/benchmark_fragment_three_objects.py"),
        Path("tests/test_fragment_repair.py"), Path("tests/test_fragment_repair_frozen.py"),
        *sorted(Path("ch3/revision").glob("*.py"))]
    hashes = {str(p): digest(p) for p in files}
    for path in files:
        dest = args.output / "sources" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    labels = read(STATIC / "reference.json")
    static_rows = read(FIXTURES / "target_cases.json")
    assert isinstance(labels, dict) and set(labels) == {r["id"] for r in static_rows}
    assert all(value in ("conflict", "need_observation", "ready") for value in labels.values())
    static_cases = [{"id": r["id"], "stage": "static_task", "snapshot": r["snapshot"]}
        for r in static_rows if labels[r["id"]] != "ready"]
    suites = {"execution": cases(), "static": static_cases}
    write(args.output / "plan.json", {"arms": ARMS, "repeats": 3, "horizon": 8,
        "suite_sizes": {k: len(v) for k, v in suites.items()}, "protocol_sha256": digest(PROTOCOL),
        "selection": "all_preexisting_nonready", "model_calls": 0, "physical_execution": False})
    print(json.dumps({"phase": "frozen", "suite_sizes": {k: len(v) for k, v in suites.items()}}), flush=True)
    gold = {}
    for suite, raw in suites.items():
        gold[suite], calls = reference(raw)
        write(args.output / f"{suite}_reference.json", {k: list(v) for k, v in gold[suite].items()})
        print(json.dumps({"phase": "reference", "suite": suite, "calls": calls}), flush=True)
    timings = []
    for repeat in range(3):
        order = ARMS[repeat * 2:] + ARMS[:repeat * 2]
        if repeat == 1:
            order = tuple(reversed(order))
        for suite, raw_cases in suites.items():
            for arm in order:
                started = perf_counter()
                d = build_compact_domain()
                lower = IndependentActionBound(d)
                certs = read(SOURCE / ("fixed_certificates.json" if arm.endswith("_fixed")
                    else "learned_certificates.json")) if arm.startswith("transfer") else []
                bank = d.regions.RegionBank(certs)
                decoded = [(r, decode(d, r["snapshot"])) for r in raw_cases]
                prepared = perf_counter()
                records = [{"id": raw["id"], "stage": raw["stage"],
                    "result": propose_frozen(d, s, bank, lower, arm.removesuffix("_fixed"), known_nonready=True)}
                    for raw, s in decoded]
                finished = perf_counter()
                aggregate = Counter()
                for (raw, s), record in zip(decoded, records):
                    result = record["result"]
                    assert signature(result) == gold[suite][raw["id"]]
                    aggregate.update(result.stats)
                    if result.status == "proposal":
                        assert result.contract.obligations == tuple(o for o in s.contract.obligations
                            if o.clause.id not in result.deleted)
                        assert set(result.deleted) <= set(editable_ids(s))
                        validate_plan(d, s, result.contract, result.plan)
                row = {"repeat": repeat, "suite": suite, "arm": arm, "stats": dict(aggregate),
                    "prep_ms": (prepared-started)*1000, "repair_ms": (finished-prepared)*1000,
                    "total_ms": (finished-started)*1000}
                timings.append(row)
                if repeat == 0:
                    write(args.output / f"{suite}_{arm}_proposals.json", records)
                print(json.dumps(row), flush=True)
    support_checks, outcomes, methods = {}, {}, {}
    d = build_compact_domain()
    bank = d.regions.RegionBank(read(SOURCE / "learned_certificates.json"))
    for suite, raw_cases in suites.items():
        support_checks[suite] = 0
        outcomes[suite] = dict(Counter(v[0] for v in gold[suite].values()))
        for raw in raw_cases:
            s = decode(d, raw["snapshot"])
            formula = SupportConstraints(d, bank, s)
            editable = editable_ids(s)
            for _, ids, task in candidates(d, s, editable, {i: 1 for i in editable}):
                assert formula.blocked(ids) == (bank.evidence(replace(s, contract=task)) is not None)
                support_checks[suite] += 1
        methods[suite] = {}
        for arm in ARMS:
            rows = [r for r in timings if r["suite"] == suite and r["arm"] == arm]
            assert all(r["stats"] == rows[0]["stats"] for r in rows)
            methods[suite][arm] = {"stats": rows[0]["stats"], "medians": {
                k: median(r[k] for r in rows) for k in ("prep_ms", "repair_ms", "total_ms")}}
        assert methods[suite]["transfer_support"]["stats"]["backend_calls"] == methods[suite]["transfer_recheck"]["stats"]["backend_calls"]
    summary = {"suite_sizes": {k: len(v) for k, v in suites.items()}, "outcomes": outcomes,
        "all_answers_exact_minimum_and_identical": True, "all_preserved_history_anchors_unchanged": True,
        "support_equals_recheck_subsets": support_checks, "methods": methods,
        "source_learning_cost_included": False, "physical_execution": False, "model_calls": 0}
    write(args.output / "timings.json", timings)
    write(args.output / "summary.json", summary)
    close(args.output, hashes, {str(f / "manifest.json"): digest(f / "manifest.json") for f in inputs})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
