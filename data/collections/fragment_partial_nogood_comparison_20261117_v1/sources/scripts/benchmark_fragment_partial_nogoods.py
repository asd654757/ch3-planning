"""Matched-source partial-state conflict generalization and mask-equivalence audit."""
import argparse
from collections import Counter
from dataclasses import asdict
from itertools import permutations
import json
from pathlib import Path
import shutil
from statistics import mean, median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_partial_nogoods import PartialNogoodBank
from ch3.revision.fragment_runtime import build_fragment_runtime
from scripts import benchmark_fragment_state_cache as base
from scripts.benchmark_fragment_three_objects import check, close, digest, load, read, resolve, write

METHODS = ("partial_source", "partial_complete", "full")
PROTOCOL = Path("docs/fragment_partial_nogood_protocol_20261117.md")


def archive(output):
    hashes = base.archive(output)
    for path in (Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
                 Path("docs/fragment_method_prior_art_20261117.md"),
                 Path("tests/test_fragment_partial_nogoods.py")):
        hashes[str(path)] = digest(path)
        dest = output / "sources" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    return hashes


def source_stage(method, path):
    began = perf_counter()
    d = build_compact_domain()
    fixed = d.regions.RegionBank(read(base.SOURCE / "fixed_certificates.json"))
    lower = IndependentActionBound(d)
    cases = load(d, base.SOURCE_FIXTURES / "source_cases.json")
    session = d.Session(fixed)
    results = [resolve(session, lower, c["snapshot"]) for c in cases]
    source_by_id = {c["id"]: c["snapshot"] for c in cases}
    acquisition_ms = (perf_counter()-began)*1000
    phase = perf_counter()
    oracle = d.old.Oracle()
    failures, patterns = [], []
    fingerprint = d.fingerprint()
    for source_id in base.SOURCE_IDS:
        snapshot = source_by_id[source_id]
        atoms = d.regions.minimize(snapshot, oracle)
        if not atoms:
            raise ValueError("frozen source core changed")
        pattern = d.regions.canonical(atoms, 8)
        patterns.append(pattern)
        failures.append({"source_id": source_id, "core": [asdict(a) for a in atoms],
            "state": asdict(snapshot.state), "key": pattern.key, "semantics_sha256": fingerprint, "horizon": 8})
    extraction_ms = (perf_counter()-phase)*1000
    phase = perf_counter()
    nogoods = []
    certificates = fixed.certificates
    if method == "full":
        new = d.regions.RegionBank.verified(patterns, oracle)
        certificates = d.regions.RegionBank.combine(fixed, new).certificates
    else:
        bank = PartialNogoodBank(d, fixed, failures, mode=method.removeprefix("partial_"), oracle=oracle)
        nogoods = bank.records
    certification_ms = (perf_counter()-phase)*1000
    phase = perf_counter()
    memo = [{"key": list(k), "status": status, "plan": [asdict(e) for e in plan]}
            for k, (status, plan) in sorted(session.memo.items())]
    payload = {"method": method, "certificates": certificates,
        "failures": failures if method != "full" else [], "nogoods": nogoods, "memo": memo}
    path.write_text(json.dumps(payload, separators=(",", ":"))+"\n")
    serialization_ms = (perf_counter()-phase)*1000
    source_ms = (perf_counter()-began)*1000
    return {"source_ms": source_ms, "acquisition_ms": acquisition_ms, "extraction_ms": extraction_ms,
        "source_certification_ms": certification_ms, "serialization_ms": serialization_ms,
        "source_backend_calls": session.backend_calls, "source_cache_size": len(memo),
        "nogood_bindings": sum(len(r["bindings"]) for r in nogoods),
        "partial_guards": sum(len(b["guards"]) for r in nogoods for b in r["bindings"]),
        "guard_lengths": Counter(len(g) for r in nogoods for b in r["bindings"] for g in b["guards"]),
        "learning_oracle": oracle.stats(), "serialized_bytes": path.stat().st_size}, {
            "failures": failures, "trace": [{"id": c["id"], "status": r.status, "origin": r.origin}
                for c, r in zip(cases, results)], "memo": memo}


def online_stage(method, path, templates):
    began = perf_counter()
    d = build_compact_domain()
    lower = IndependentActionBound(d)
    payload = read(path)
    fixed_or_full = d.regions.RegionBank(payload["certificates"])
    bank = fixed_or_full if method == "full" else PartialNogoodBank(d, fixed_or_full, payload["failures"],
        mode=method.removeprefix("partial_"), expected_records=payload["nogoods"])
    session = d.Session(bank)
    session.memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"])) for r in payload["memo"]}
    session.freeze()
    bridge = build_fragment_runtime(d, session, lower)
    preparation_ms = (perf_counter()-began)*1000
    began = perf_counter()
    traces = base.execution.run_loop(d, bridge, templates)
    loop_ms = (perf_counter()-began)*1000
    assert len(session.memo) == len(payload["memo"]) and not session.patterns
    assert bridge.unchanged_runtime_methods
    assert bank.evidence.__func__.__code__ is d.regions.RegionBank.evidence.__code__
    metrics = {"preparation_ms": preparation_ms, "execution_loop_ms": loop_ms,
        "online_total_ms": preparation_ms+loop_ms, "decision_ms": sum(r["elapsed_ms"] for r in bridge.records),
        "backend_calls": session.backend_calls, "origins": Counter(r["origin"] for r in bridge.records),
        "completed_revisions": sum(r["completed"] for r in traces),
        "initial_statuses": Counter(r["initial_status"] for r in traces),
        "suspended_during_execution": sum(r["initial_status"] == "ready" and not r["completed"] for r in traces),
        "action_attempts": sum(len(r["actions"]) for r in traces),
        "observed_events": sum(len(a["events"]) for r in traces for a in r["actions"]),
        "load_oracles": [o.stats() for o in d.batch_oracles]}
    return metrics, d, bridge, traces


def comparison(output):
    for folder in (base.SOURCE, base.SOURCE_FIXTURES, base.FIXTURES, base.PRIOR):
        check(folder)
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    templates = read(base.FIXTURES / "episodes.json")
    expected_memo = read(base.SOURCE / "shared_memo.json")
    expected_source = [{k: r[k] for k in ("id", "status", "origin")} for r in read(base.SOURCE / "fixed_source_trace.json")]
    proofs = {r["source_id"]: r for r in read(base.PROOFS)}
    old_digest = read(base.PRIOR / "timings.json")[0]["semantic_trace_sha256"]
    orders = list(permutations(METHODS))
    write(output / "plan.json", {"rounds": 10, "methods": METHODS,
        "order": [orders[i % 6] for i in range(10)], "source_ids": base.SOURCE_IDS,
        "tasks_changed": False, "target_learning": False, "physical_execution": False, "source_hashes": hashes})
    timings, reference, first = [], {}, {}
    for repeat in range(10):
        for method in orders[repeat % 6]:
            path = output / f"source_r{repeat}_{method}.json"
            source_metrics, source_audit = source_stage(method, path)
            metrics, d, bridge, traces = online_stage(method, path, templates)
            # All reference and provenance checks follow the method clocks.
            assert source_metrics["source_backend_calls"] == source_metrics["source_cache_size"] == 333
            assert source_audit["memo"] == expected_memo and source_audit["trace"] == expected_source
            for failure in source_audit["failures"]:
                expected = proofs[failure["source_id"]]
                assert failure["core"] == expected["core"] and failure["key"] == expected["key"]
                assert failure["state"] == expected["state"]
            checks = base.execution.audit(d, bridge, traces, reference)
            semantic_digest = base.execution.trace_digest(traces, output)
            assert semantic_digest == old_digest
            row = {"repeat": repeat, "method": method, **source_metrics, **metrics, **checks,
                "total_algorithm_ms": source_metrics["source_ms"]+metrics["online_total_ms"],
                "semantic_trace_sha256": semantic_digest}
            if repeat == 0:
                first[method] = bridge.records
                write(output / f"{method}_decisions.json", bridge.records)
                write(output / f"{method}_source_failures.json", source_audit["failures"])
                if method == "full":
                    write(output / "semantic_trace.json", traces)
            else:
                assert [(r["origin"], r["status"], r["cursor"], r["proof"]) for r in bridge.records] == [
                    (r["origin"], r["status"], r["cursor"], r["proof"]) for r in first[method]]
            timings.append(row)
            write(output / "timings.json", timings)
            print(json.dumps({k: row[k] for k in ("repeat", "method", "source_ms", "preparation_ms",
                "execution_loop_ms", "total_algorithm_ms", "backend_calls", "partial_guards")}), flush=True)
    fields = ("source_ms", "preparation_ms", "decision_ms", "execution_loop_ms", "online_total_ms", "total_algorithm_ms")
    summary = {"rounds": 10, "episodes": 32, "revisions": 384, "methods": {}, "target_learning": False,
        "target_cache_growth": False, "physical_execution": False, "model_calls": 0,
        "reference_unique_snapshots": len(reference), "all_semantic_traces_match_prior": True}
    for method in METHODS:
        rows = [r for r in timings if r["method"] == method]
        summary["methods"][method] = {"medians": {k: median(r[k] for r in rows) for k in fields},
            "means": {k: mean(r[k] for r in rows) for k in fields},
            **{k: rows[0][k] for k in ("backend_calls", "origins", "completed_revisions", "initial_statuses",
                "suspended_during_execution", "action_attempts", "observed_events", "source_backend_calls",
                "source_cache_size", "nogood_bindings", "partial_guards", "guard_lengths", "serialized_bytes",
                "decisions_checked", "ready_suffixes_checked", "false_conflicts", "unsafe_ready")}}
    summary["full_comparisons"] = {}
    for control in METHODS[:-1]:
        a, b = summary["methods"][control], summary["methods"]["full"]
        paired = {k: [next(r[k] for r in timings if r["repeat"] == i and r["method"] == control)
            - next(r[k] for r in timings if r["repeat"] == i and r["method"] == "full") for i in range(10)] for k in fields}
        summary["full_comparisons"][control] = {"backend_calls_saved": a["backend_calls"]-b["backend_calls"],
            "median_reduction_percent": {k: (a["medians"][k]-b["medians"][k])/a["medians"][k]*100 for k in fields},
            "mean_reduction_percent": {k: (a["means"][k]-b["means"][k])/a["means"][k]*100 for k in fields},
            "paired_savings_ms": paired, "paired_wins": {k: sum(x > 0 for x in v) for k, v in paired.items()}}
    assert summary["methods"]["partial_complete"]["backend_calls"] == summary["methods"]["full"]["backend_calls"]
    write(output / "summary.json", summary)
    close(output, hashes, {**{str(f / "manifest.json"): digest(f / "manifest.json") for f in
        (base.SOURCE, base.SOURCE_FIXTURES, base.FIXTURES, base.PRIOR)}, str(base.PROOFS): digest(base.PROOFS)})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    comparison(parser.parse_args().output)
