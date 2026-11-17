"""Matched-source three-arm mechanism comparison on frozen execution episodes."""
import argparse
from collections import Counter
from dataclasses import asdict
from itertools import permutations
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.fragment_state_cache import StateBoundBank
from scripts import benchmark_fragment_execution as execution
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, load, read, resolve, write

PROTOCOL = Path("docs/fragment_state_cache_protocol_20261117.md")
SOURCE = ROOT / "fragment_compact_source_20261116_v1"
SOURCE_FIXTURES = ROOT / "fragment_three_fixtures_20261116_v2"
PROOFS = ROOT / "fragment_three_analysis_20261116_v1/source_core_proofs.json"
FIXTURES = ROOT / "fragment_execution_fixtures_20261116_v1"
PRIOR = ROOT / "fragment_execution_validation_20261116_v1"
METHODS = ("fixed", "state_cache", "full")
SOURCE_IDS = ("source-0008", "source-0016", "source-0019", "source-0020",
              "source-0029", "source-0067", "source-0091", "source-0115")


def archive(output):
    import shutil
    hashes = execution.archive(output)
    for p in (Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
              Path("tests/test_fragment_state_cache.py")):
        hashes[str(p)] = digest(p)
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def source_stage(method, path):
    began = perf_counter()
    d = build_compact_domain()
    fixed = d.regions.RegionBank(read(SOURCE / "fixed_certificates.json"))
    lower = IndependentActionBound(d)
    cases = load(d, SOURCE_FIXTURES / "source_cases.json")
    session = d.Session(fixed)
    results = [resolve(session, lower, c["snapshot"]) for c in cases]
    source_by_id = {c["id"]: c["snapshot"] for c in cases}
    acquisition_ms = (perf_counter()-began)*1000
    phase = perf_counter()
    failures, patterns = [], []
    oracle = d.old.Oracle()
    if method != "fixed":
        for source_id in SOURCE_IDS:
            snapshot = source_by_id[source_id]
            atoms = d.regions.minimize(snapshot, oracle)
            if not atoms:
                raise ValueError("frozen source failure no longer yields a core")
            pattern = d.regions.canonical(atoms, 8)
            patterns.append(pattern)
            failures.append({"source_id": source_id, "core": [asdict(a) for a in atoms],
                "state": asdict(snapshot.state), "key": pattern.key,
                "semantics_sha256": d.fingerprint(), "horizon": 8})
    extraction_ms = (perf_counter()-phase)*1000
    phase = perf_counter()
    certificates = fixed.certificates
    points = 0
    if method == "full":
        new = d.regions.RegionBank.verified(patterns, oracle)
        certificates = d.regions.RegionBank.combine(fixed, new).certificates
    elif method == "state_cache":
        bank = StateBoundBank(d, fixed, failures, oracle)
        points = len(bank.entries)
    certification_ms = (perf_counter()-phase)*1000
    phase = perf_counter()
    memo = [{"key": list(k), "status": status, "plan": [asdict(e) for e in plan]}
            for k, (status, plan) in sorted(session.memo.items())]
    payload = {"method": method, "certificates": certificates,
        "failures": failures if method == "state_cache" else [], "memo": memo}
    path.write_text(json.dumps(payload, separators=(",", ":"))+"\n")
    serialization_ms = (perf_counter()-phase)*1000
    source_ms = (perf_counter()-began)*1000
    return {"source_ms": source_ms, "acquisition_ms": acquisition_ms,
        "extraction_ms": extraction_ms, "source_certification_ms": certification_ms,
        "serialization_ms": serialization_ms, "source_backend_calls": session.backend_calls,
        "source_cache_size": len(memo), "source_origins": Counter(r.origin for r in results),
        "new_core_keys": [p.key for p in patterns], "state_cache_points": points,
        "learning_oracle": oracle.stats(), "serialized_bytes": path.stat().st_size}, {
            "failures": failures, "trace": [{"id": c["id"], "status": r.status, "origin": r.origin}
                                           for c, r in zip(cases, results)], "memo": memo}


def online_stage(method, path, templates):
    began = perf_counter()
    d = build_compact_domain()
    lower = IndependentActionBound(d)
    payload = read(path)
    fixed_or_full = d.regions.RegionBank(payload["certificates"])
    bank = StateBoundBank(d, fixed_or_full, payload["failures"]) if method == "state_cache" else fixed_or_full
    session = d.Session(bank)
    session.memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"]))
                    for r in payload["memo"]}
    session.freeze()
    bridge = build_fragment_runtime(d, session, lower)
    preparation_ms = (perf_counter()-began)*1000
    began = perf_counter()
    traces = execution.run_loop(d, bridge, templates)
    loop_ms = (perf_counter()-began)*1000
    assert len(session.memo) == len(payload["memo"]) and not session.patterns
    assert bridge.unchanged_runtime_methods
    metrics = {"preparation_ms": preparation_ms, "execution_loop_ms": loop_ms,
        "online_total_ms": preparation_ms+loop_ms,
        "decision_ms": sum(r["elapsed_ms"] for r in bridge.records),
        "backend_calls": session.backend_calls, "origins": Counter(r["origin"] for r in bridge.records),
        "completed_revisions": sum(r["completed"] for r in traces),
        "initial_statuses": Counter(r["initial_status"] for r in traces),
        "suspended_during_execution": sum(r["initial_status"] == "ready" and not r["completed"] for r in traces),
        "action_attempts": sum(len(r["actions"]) for r in traces),
        "observed_events": sum(len(a["events"]) for r in traces for a in r["actions"]),
        "load_oracles": [o.stats() for o in d.batch_oracles]}
    return metrics, d, bridge, traces


def comparison(output):
    for folder in (SOURCE, SOURCE_FIXTURES, FIXTURES, PRIOR):
        check(folder)
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    templates = read(FIXTURES / "episodes.json")
    source_expected = read(SOURCE / "fixed_source_trace.json")
    expected_memo = read(SOURCE / "shared_memo.json")
    expected_proofs = {r["source_id"]: r for r in read(PROOFS)}
    expected_trace = read(PRIOR / "summary.json")["methods"]["fixed"]
    old_digest = read(PRIOR / "timings.json")[0]["semantic_trace_sha256"]
    orders = list(permutations(METHODS))
    write(output / "plan.json", {"rounds": 10, "methods": METHODS,
        "order": [orders[i % len(orders)] for i in range(10)], "source_ids": SOURCE_IDS,
        "tasks_changed": False, "target_learning": False, "physical_execution": False,
        "source_hashes": hashes, "input_proofs_sha256": digest(PROOFS)})
    reference, timings = {}, []
    first_records = {}
    for repeat in range(10):
        for method in orders[repeat % len(orders)]:
            path = output / f"source_r{repeat}_{method}.json"
            source_metrics, source_audit = source_stage(method, path)
            metrics, d, bridge, traces = online_stage(method, path, templates)
            # Full source/target audits start only after both clocks have ended.
            assert source_metrics["source_backend_calls"] == 333
            assert source_metrics["source_cache_size"] == 333
            assert source_audit["memo"] == expected_memo
            assert source_audit["trace"] == [{k: r[k] for k in ("id", "status", "origin")} for r in source_expected]
            for failure in source_audit["failures"]:
                proof = expected_proofs[failure["source_id"]]
                assert failure["core"] == proof["core"] and failure["key"] == proof["key"]
                assert failure["state"] == proof["state"]
            checks = execution.audit(d, bridge, traces, reference)
            semantic_digest = execution.trace_digest(traces, output)
            assert semantic_digest == old_digest
            assert metrics["completed_revisions"] == expected_trace["completed_revisions"]
            row = {"repeat": repeat, "method": method, **source_metrics, **metrics, **checks,
                "total_algorithm_ms": source_metrics["source_ms"]+metrics["online_total_ms"],
                "semantic_trace_sha256": semantic_digest}
            if repeat == 0:
                first_records[method] = bridge.records
                write(output / f"{method}_decisions.json", bridge.records)
                if method == "fixed":
                    write(output / "semantic_trace.json", traces)
                write(output / f"{method}_source_failures.json", source_audit["failures"])
            else:
                assert [(r["origin"], r["status"], r["cursor"], r["proof"]) for r in bridge.records] == [
                    (r["origin"], r["status"], r["cursor"], r["proof"]) for r in first_records[method]]
            timings.append(row)
            write(output / "timings.json", timings)
            print(json.dumps({k: row[k] for k in ("repeat", "method", "source_ms", "preparation_ms",
                "execution_loop_ms", "online_total_ms", "total_algorithm_ms", "backend_calls", "origins")}), flush=True)
    metrics = ("source_ms", "preparation_ms", "decision_ms", "execution_loop_ms", "online_total_ms", "total_algorithm_ms")
    summary = {"rounds": 10, "episodes": 32, "revisions": 384, "methods": {},
        "target_learning": False, "target_cache_growth": False, "physical_execution": False,
        "model_calls": 0, "reference_unique_snapshots": len(reference), "all_semantic_traces_match_prior": True,
        "cost_scope": "matched_source_collection_extraction_certification_reload_and_symbolic_execution"}
    for method in METHODS:
        rows = [r for r in timings if r["method"] == method]
        summary["methods"][method] = {"medians": {k: median(r[k] for r in rows) for k in metrics},
            **{k: rows[0][k] for k in ("backend_calls", "origins", "completed_revisions", "initial_statuses",
                "suspended_during_execution", "action_attempts", "observed_events", "source_backend_calls",
                "source_cache_size", "state_cache_points", "serialized_bytes", "decisions_checked",
                "ready_suffixes_checked", "false_conflicts", "unsafe_ready")}}
    summary["full_comparisons"] = {}
    for control in ("fixed", "state_cache"):
        a, b = summary["methods"][control], summary["methods"]["full"]
        savings = {k: [next(r[k] for r in timings if r["repeat"] == i and r["method"] == control) -
            next(r[k] for r in timings if r["repeat"] == i and r["method"] == "full") for i in range(10)] for k in metrics}
        summary["full_comparisons"][control] = {"backend_calls_saved": a["backend_calls"]-b["backend_calls"],
            "median_reduction_percent": {k: (a["medians"][k]-b["medians"][k])/a["medians"][k]*100 for k in metrics},
            "paired_savings_ms": savings, "paired_wins": {k: sum(x > 0 for x in values) for k, values in savings.items()}}
    write(output / "summary.json", summary)
    close(output, hashes, {**{str(f / "manifest.json"): digest(f / "manifest.json")
        for f in (SOURCE, SOURCE_FIXTURES, FIXTURES, PRIOR)}, str(PROOFS): digest(PROOFS)})
    print(json.dumps(summary), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    comparison(args.output)


if __name__ == "__main__":
    main()
