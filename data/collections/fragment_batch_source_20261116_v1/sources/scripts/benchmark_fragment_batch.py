"""Equivalence and full-cost replay of the shared batch certification engine."""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.batch_fragment_oracle import build_batch_domain
from ch3.revision.fragments import Atom, Pattern
from scripts.benchmark_fragment_three_objects import (
    ROOT, archive, check, close, digest, load, read, resolve, verify_plan, write,
)

FIXTURES = ROOT / "fragment_three_fixtures_20261116_v2"
OLD_SOURCE = ROOT / "fragment_three_source_20261116_v1"
OLD_TARGET = ROOT / "fragment_three_validation_20261116_v1"
PROTOCOL = Path("docs/fragment_batch_protocol_20261116.md")


def own_sources(output):
    hashes = archive(output)
    files = [Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
             Path("ch3/revision/batch_fragment_oracle.py"), Path("tests/test_batch_fragment_oracle.py"),
             Path("scripts/benchmark_fragment_three_objects.py")]
    for p in files:
        hashes[str(p)] = digest(p)
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def pattern(raw):
    return Pattern(tuple(Atom(**a) for a in raw["atoms"]),
                   tuple(tuple(v) for v in raw["variables"]), raw["horizon"])


def equivalent_certificates(output):
    for folder in (FIXTURES, OLD_SOURCE):
        check(folder)
    output.mkdir(parents=True, exist_ok=False)
    hashes = own_sources(output)
    domain = build_batch_domain()
    oracle = domain.old.Oracle()
    records = []
    start = perf_counter()
    for expected in read(OLD_SOURCE / "learned_certificates.json"):
        actual = domain.regions.certify_region(pattern(expected["pattern"]), oracle)
        a, b = dict(actual), dict(expected)
        a.pop("semantics_sha256")
        b.pop("semantics_sha256")
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), expected["key"]
        records.append({"key": expected["key"], "exact_equal_except_engine_fingerprint": True,
                        "state_results": sum(len(v["states"]) for v in expected["bindings"])})
    elapsed = (perf_counter() - start) * 1000
    write(output / "equivalence.json", {"certificates": records, "n": len(records),
          "state_results": sum(r["state_results"] for r in records),
          "all_results_plans_bindings_masks_equal": True, "audit_wall_ms": elapsed,
          "engine_stats": oracle.stats(), "unchanged_algorithms": domain.unchanged_algorithms})
    close(output, hashes, {str(OLD_SOURCE / "manifest.json"): digest(OLD_SOURCE / "manifest.json")})
    print(json.dumps(read(output / "equivalence.json")), flush=True)


def save_memo(path, memo):
    from dataclasses import asdict
    write(path, [{"key": list(k), "status": status, "plan": [asdict(e) for e in plan]}
                 for k, (status, plan) in sorted(memo.items())])


def source(equivalence, output):
    from ch3.revision.fragment_domain_adapter import IndependentActionBound
    check(equivalence)
    check(FIXTURES)
    check(OLD_SOURCE)
    output.mkdir(parents=True, exist_ok=False)
    hashes = own_sources(output)
    old_traces = {name: read(OLD_SOURCE / f"{name}_source_trace.json") for name in ("fixed", "learned")}
    old_keys = {p["key"] for p in read(OLD_SOURCE / "learned_certificates.json")}
    timings = []
    first_keys = {}
    shared_keys = None
    for repeat in range(3):
        sessions = {}
        round_rows = []
        for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
            began = perf_counter()
            phase = perf_counter()
            d = build_batch_domain()
            domain_ms = (perf_counter() - phase) * 1000
            phase = perf_counter()
            cases = load(d, FIXTURES / "source_cases.json")
            patterns = [pattern(c["pattern"]) for c in read(OLD_SOURCE / "fixed_certificates.json")]
            input_ms = (perf_counter() - phase) * 1000
            phase = perf_counter()
            oracle = d.old.Oracle()
            bank = d.regions.RegionBank.verified(patterns, oracle)
            certification_ms = (perf_counter() - phase) * 1000
            phase = perf_counter()
            lower = IndependentActionBound(d)
            bound_ms = (perf_counter() - phase) * 1000
            phase = perf_counter()
            session = d.Session(bank, learn=name == "learned")
            results = []
            for case in cases:
                results.append(resolve(session, lower, case["snapshot"]))
                if len(session.patterns) >= 8:
                    session.learning = False
            source_ms = (perf_counter() - phase) * 1000
            preparation_ms = (perf_counter() - began) * 1000
            for c, result, original in zip(cases, results, old_traces[name]):
                assert c["id"] == original["id"] and result.status == original["status"]
                assert result.origin == original["origin"] and result.new_pattern == original["new_pattern"]
                verify_plan(d, c["snapshot"], result)
            if name == "learned":
                assert {c["key"] for c in session.bank.certificates} == old_keys
            if repeat == 0:
                write(output / f"{name}_certificates.json", session.bank.certificates)
                write(output / f"{name}_patterns.json", list(session.patterns.values()))
                write(output / f"{name}_source_trace.json", [
                    {"id": c["id"], "status": r.status, "origin": r.origin,
                     "new_pattern": r.new_pattern} for c, r in zip(cases, results)])
                first_keys[name] = set(session.memo)
            else:
                assert set(session.memo) == first_keys[name]
            row = {"repeat": repeat, "method": name, "domain_init_ms": domain_ms,
                   "source_and_pattern_input_ms": input_ms, "initial_certification_ms": certification_ms,
                   "lower_bound_ms": bound_ms, "source_processing_ms": source_ms,
                   "preparation_ms_before_cache_merge": preparation_ms,
                   "source_backend_calls": session.backend_calls, "new_patterns": len(session.patterns),
                   "initial_oracle": oracle.stats(), "learning_oracle": session.oracle.stats(),
                   "origins": Counter(r.origin for r in results)}
            round_rows.append(row)
            sessions[name] = session
            print(json.dumps(row), flush=True)
        start = perf_counter()
        shared = dict(sessions["fixed"].memo)
        for key, value in sessions["learned"].memo.items():
            if key in shared:
                assert shared[key][0] == value[0]
            else:
                shared[key] = value
        merge_ms = (perf_counter() - start) * 1000
        if repeat == 0:
            save_memo(output / "shared_memo.json", shared)
            shared_keys = set(shared)
        else:
            assert set(shared) == shared_keys
        for row in round_rows:
            row["cache_merge_ms"] = merge_ms
            row["preparation_ms"] = row["preparation_ms_before_cache_merge"] + merge_ms
            timings.append(row)
        write(output / "timings.json", timings)
    summary = {"n": 384, "methods": {}, "target_files_read_during_source": False,
               "all_source_outcomes_origins_and_core_keys_equal_old": True,
               "model_calls": 0, "physical_execution": False}
    for name in ("fixed", "learned"):
        rows = [r for r in timings if r["method"] == name]
        summary["methods"][name] = {"median_source_preparation_ms": median(r["preparation_ms"] for r in rows),
                                   "source_backend_calls": rows[0]["source_backend_calls"],
                                   "initial_oracle": rows[0]["initial_oracle"],
                                   "learning_oracle": rows[0]["learning_oracle"]}
    write(output / "summary.json", summary)
    close(output, hashes, {str(FIXTURES / "manifest.json"): digest(FIXTURES / "manifest.json"),
          str(equivalence / "manifest.json"): digest(equivalence / "manifest.json"),
          str(OLD_SOURCE / "manifest.json"): digest(OLD_SOURCE / "manifest.json")})
    print(json.dumps(summary), flush=True)


def validation(training, output):
    from ch3.revision.fragment_domain_adapter import IndependentActionBound
    check(training)
    check(FIXTURES)
    check(OLD_TARGET)
    output.mkdir(parents=True, exist_ok=False)
    hashes = own_sources(output)
    reference = read(OLD_TARGET / "reference.json")
    old_source_times = read(training / "timings.json")
    records = []
    by_group = {}
    for repeat in range(3):
        indices = list(range(768))
        random.Random(11673 + repeat).shuffle(indices)
        for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
            start = perf_counter()
            d = build_batch_domain()
            lower = IndependentActionBound(d)
            before = len(d.batch_oracles)
            bank = d.regions.RegionBank(read(training / f"{name}_certificates.json"))
            recert_oracles = d.batch_oracles[before:]
            session = d.Session(bank)
            memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"]))
                    for r in read(training / "shared_memo.json")}
            session.memo = dict(memo)
            session.freeze()
            target_preparation_ms = (perf_counter() - start) * 1000
            # Common target data parsing/verification is outside method timers.
            cases = load(d, FIXTURES / "target_cases.json")
            start = perf_counter()
            results = [resolve(session, lower, cases[i]["snapshot"]) for i in indices]
            elapsed = (perf_counter() - start) * 1000
            for index, result in zip(indices, results):
                assert result.status == reference[cases[index]["id"]]
                verify_plan(d, cases[index]["snapshot"], result)
            assert len(session.memo) == len(memo) and not session.patterns
            source_row = next(r for r in old_source_times if r["repeat"] == repeat and r["method"] == name)
            total = source_row["preparation_ms"] + target_preparation_ms + elapsed
            row = {"repeat": repeat, "method": name, "query_ms": elapsed,
                   "target_preparation_ms": target_preparation_ms,
                   "source_preparation_ms": source_row["preparation_ms"], "total_algorithm_ms_estimate": total,
                   "target_backend_calls": session.backend_calls,
                   "load_recertification_oracles": [o.stats() for o in recert_oracles],
                   "ready_plans_replayed": sum(r.status == "ready" for r in results),
                   "origins": Counter(r.origin for r in results)}
            records.append(row)
            if repeat == 0:
                groups = {}
                for index, result in zip(indices, results):
                    group = groups.setdefault(cases[index]["group"], Counter())
                    group["n"] += 1
                    group["backend_calls"] += result.origin == "backend"
                by_group[name] = groups
            write(output / "timings.json", records)
            print(json.dumps(row), flush=True)
    summary = {"n": 768, "methods": {}, "groups": by_group,
               "all_statuses_match_old_full_backend": True, "target_learning": False,
               "target_cache_growth": False, "retrospective_implementation_replay": True,
               "model_calls": 0, "physical_execution": False}
    for name in ("fixed", "learned"):
        rows = [r for r in records if r["method"] == name]
        summary["methods"][name] = {"median_query_ms": median(r["query_ms"] for r in rows),
                                   "median_target_preparation_ms": median(r["target_preparation_ms"] for r in rows),
                                   "median_total_algorithm_ms_estimate": median(r["total_algorithm_ms_estimate"] for r in rows),
                                   "target_backend_calls": rows[0]["target_backend_calls"]}
    a, b = summary["methods"]["fixed"], summary["methods"]["learned"]
    summary["target_calls_saved"] = a["target_backend_calls"] - b["target_backend_calls"]
    assert summary["target_calls_saved"] == 66
    summary["query_reduction_percent"] = (a["median_query_ms"] - b["median_query_ms"]) / a["median_query_ms"] * 100
    summary["full_cost_reduction_percent"] = (a["median_total_algorithm_ms_estimate"] - b["median_total_algorithm_ms_estimate"]) / a["median_total_algorithm_ms_estimate"] * 100
    summary["paired_full_cost_saved_ms"] = [
        next(r["total_algorithm_ms_estimate"] for r in records if r["repeat"] == i and r["method"] == "fixed")
        - next(r["total_algorithm_ms_estimate"] for r in records if r["repeat"] == i and r["method"] == "learned")
        for i in range(3)]
    write(output / "summary.json", summary)
    check(training)
    check(FIXTURES)
    close(output, hashes, {str(training / "manifest.json"): digest(training / "manifest.json"),
          str(FIXTURES / "manifest.json"): digest(FIXTURES / "manifest.json"),
          str(OLD_TARGET / "manifest.json"): digest(OLD_TARGET / "manifest.json")})
    print(json.dumps(summary), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("equivalence", "source", "validation"), required=True)
    p.add_argument("--equivalence", type=Path)
    p.add_argument("--training", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.phase == "equivalence":
        equivalent_certificates(args.output)
    elif args.phase == "source" and args.equivalence is not None:
        source(args.equivalence, args.output)
    elif args.phase == "validation" and args.training is not None:
        validation(args.training, args.output)
    else:
        p.error("source requires --equivalence; validation requires --training")


if __name__ == "__main__":
    main()
