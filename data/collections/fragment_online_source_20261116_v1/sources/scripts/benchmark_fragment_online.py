"""Measure both controls with one online cache/core/fallback implementation."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import shutil
from statistics import median
from time import perf_counter

from ch3.context_memory.cases import load_cases
from ch3.revision.fragments import Oracle
from ch3.revision.fragment_regions import RegionBank, expert_patterns
from ch3.revision.fragment_session import FragmentSession
from ch3.revision.history import Event, monitor, successors
from scripts.audit_fragment_strong_control import PRIOR, ROOT, SOURCE, read_pattern
from scripts.benchmark_revision_fragments import signature
from scripts.benchmark_revision_recovery_pilot import write

PROTOCOL = Path("docs/fragment_online_protocol_20261116.md")


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def copy_sources(output):
    files = [Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
             Path("ch3/revision/fragment_session.py"), Path("tests/test_fragment_session.py"),
             Path("ch3/revision/fragment_regions.py"), Path("ch3/revision/fragments.py"),
             Path("ch3/revision/history.py"), Path("ch3/context_memory/cases.py"),
             Path("scripts/audit_fragment_strong_control.py"),
             Path("scripts/benchmark_revision_fragments.py"),
             Path("scripts/benchmark_revision_recovery_pilot.py")]
    hashes = {str(p): digest(p) for p in files}
    for p in files:
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def verify_plan(snapshot, result):
    if result.status != "ready":
        assert not result.plan
        return
    state = snapshot.state
    assert len(result.plan) <= 8
    for offset, event in enumerate(result.plan, 1):
        assert event.seq == snapshot.cursor + offset
        state = next(ns for ns, e in successors(state, event.seq) if e == event)
    # Static target requests have no uncertain history alternatives.
    assert not snapshot.history
    assert monitor(snapshot.contract, state, result.plan) == (frozenset(), frozenset())


def save_memo(path, memo):
    write(path, [{"key": list(key), "status": status, "plan": [asdict(e) for e in plan]}
                 for key, (status, plan) in sorted(memo.items())])


def source(output):
    output.mkdir(parents=True, exist_ok=False)
    hashes = copy_sources(output)
    start = perf_counter()
    cases = load_cases(SOURCE)
    random.Random(11661).shuffle(cases)
    load_ms = (perf_counter() - start) * 1000
    prior_manifest = json.loads((PRIOR / "manifest.json").read_text())
    for name, expected in prior_manifest["frozen_artifact_hashes"].items():
        assert digest(PRIOR / name) == expected
    patterns = [*expert_patterns()]
    for file in ("seed_patterns.json", "learned_patterns.json"):
        patterns.extend(read_pattern(p) for p in json.loads((PRIOR / file).read_text()))
    timings, first, statuses = [], {}, {}
    for repeat in range(3):
        for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
            build_start = perf_counter()
            proof = Oracle()
            session = FragmentSession(RegionBank.verified(patterns, proof), learn=name == "learned")
            build_ms = (perf_counter() - build_start) * 1000
            start = perf_counter()
            results = [session.resolve(c["snapshot"]) for c in cases]
            source_ms = (perf_counter() - start) * 1000
            outcome = [r.status for r in results]
            if not statuses:
                statuses["reference"] = outcome
            assert outcome == statuses["reference"]
            for c, result in zip(cases, results):
                if not c["snapshot"].history:
                    verify_plan(c["snapshot"], result)
            if name in first:
                assert session.patterns.keys() == first[name].patterns.keys()
                assert session.backend_calls == first[name].backend_calls
                assert session.memo.keys() == first[name].memo.keys()
            else:
                first[name] = session
                write(output / f"{name}_certificates.json", session.bank.certificates)
                write(output / f"{name}_patterns.json", list(session.patterns.values()))
                write(output / f"{name}_source_trace.json", [
                    {"id": c["id"], "status": r.status, "origin": r.origin,
                     "new_pattern": r.new_pattern} for c, r in zip(cases, results)])
            row = {"repeat": repeat, "method": name, "build_ms": build_ms,
                   "source_ms": source_ms, "preparation_ms": load_ms + build_ms + source_ms,
                   "backend_calls": session.backend_calls,
                   "fixed_certification_calls": proof.calls,
                   "learning_oracle_calls": session.oracle.calls,
                   "patterns_learned": len(session.patterns), "memo_keys": len(session.memo),
                   "origins": Counter(r.origin for r in results)}
            timings.append(row)
            write(output / "timings.json", timings)
            print(json.dumps(row), flush=True)
    start = perf_counter()
    shared = dict(first["fixed"].memo)
    for key, value in first["learned"].memo.items():
        if key in shared:
            assert shared[key][0] == value[0]
        else:
            shared[key] = value
    merge_ms = (perf_counter() - start) * 1000
    save_memo(output / "shared_memo.json", shared)
    write(output / "source_order.json", [c["id"] for c in cases])
    summary = {"n": len(cases), "input_load_ms": load_ms, "memo_merge_ms": merge_ms,
               "shared_memo_keys": len(shared), "methods": {}, "model_calls": 0,
               "physical_execution": False, "retrospective": True,
               "target_files_read_during_source": False}
    for name in first:
        rows = [r for r in timings if r["method"] == name]
        summary["methods"][name] = {
            "median_preparation_ms": median(r["preparation_ms"] for r in rows) + merge_ms,
            "backend_calls": rows[0]["backend_calls"],
            "learning_oracle_calls": rows[0]["learning_oracle_calls"],
            "patterns_learned": rows[0]["patterns_learned"]}
    write(output / "summary.json", summary)
    artifacts = {p.name: digest(p) for p in output.glob("*.json")}
    write(output / "manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
          "source_hashes": hashes, "artifact_hashes": artifacts,
          "input_hashes": {str(SOURCE): digest(SOURCE),
                           str(PRIOR / "manifest.json"): digest(PRIOR / "manifest.json")}})
    assert all(digest(p) == h for p, h in hashes.items())
    print(json.dumps(summary), flush=True)


def check_source(training):
    m = json.loads((training / "manifest.json").read_text())
    for name, expected in m["artifact_hashes"].items():
        assert digest(training / name) == expected, name
    for source, expected in {**m["source_hashes"], **m["input_hashes"]}.items():
        assert digest(source) == expected, source


def transfer(training, output):
    check_source(training)
    output.mkdir(parents=True, exist_ok=False)
    hashes = copy_sources(output)
    start = perf_counter()
    raw_memo = json.loads((training / "shared_memo.json").read_text())
    memo = {tuple(r["key"]): (r["status"], tuple(Event(**e) for e in r["plan"])) for r in raw_memo}
    memo_load_ms = (perf_counter() - start) * 1000
    sessions, loads = {}, {}
    for name in ("fixed", "learned"):
        start = perf_counter()
        session = FragmentSession(RegionBank(json.loads((training / f"{name}_certificates.json").read_text())))
        session.memo = dict(memo)
        session.freeze()
        loads[name] = (perf_counter() - start) * 1000
        sessions[name] = session
    seen_paths = [SOURCE, ROOT / "context_memory_development_20261113_v1/cases.json",
                  ROOT / "context_memory_validation_20261116_v2/cases.json"]
    seen = {signature(c["snapshot"]) for p in seen_paths for c in load_cases(p)}
    target = ROOT / "fragment_regions_validation_20261116_v1"
    target_manifest = json.loads((target / "manifest.json").read_text())
    assert digest(target / "cases.json") == target_manifest["frozen_artifact_hashes"]["cases.json"]
    cases = [c for c in load_cases(target / "cases.json") if signature(c["snapshot"]) not in seen]
    reference = {r["id"]: r["status"] for r in json.loads((target / "decisions.json").read_text())}
    records = []
    for repeat in range(3):
        indices = list(range(len(cases)))
        random.Random(11671 + repeat).shuffle(indices)
        for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
            session = sessions[name]
            old_calls = session.backend_calls
            start = perf_counter()
            results = [session.resolve(cases[i]["snapshot"]) for i in indices]
            elapsed = (perf_counter() - start) * 1000
            for i, result in zip(indices, results):
                s = cases[i]["snapshot"]
                assert result.status == reference[cases[i]["id"]]
                verify_plan(s, result)
            assert len(session.memo) == len(memo) and not session.patterns
            row = {"repeat": repeat, "method": name, "wall_ms": elapsed,
                   "backend_calls": session.backend_calls - old_calls,
                   "origins": Counter(r.origin for r in results),
                   "verified_ready_plans": sum(r.status == "ready" for r in results)}
            records.append(row)
            write(output / "timings.json", records)
            print(json.dumps(row), flush=True)
    source_summary = json.loads((training / "summary.json").read_text())
    summary = {"n": len(cases), "methods": {}, "model_calls": 0,
               "physical_execution": False, "retrospective": True,
               "target_cache_growth": False, "target_learning": False,
               "memo_load_ms_common": memo_load_ms}
    for name in sessions:
        rows = [r for r in records if r["method"] == name]
        preparation = source_summary["methods"][name]["median_preparation_ms"] + loads[name] + memo_load_ms
        query = median(r["wall_ms"] for r in rows)
        summary["methods"][name] = {"median_query_ms": query,
                                    "full_preparation_ms": preparation,
                                    "median_total_ms": preparation + query,
                                    "backend_calls": rows[0]["backend_calls"],
                                    "verified_ready_plans": rows[0]["verified_ready_plans"]}
    write(output / "summary.json", summary)
    write(output / "manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
          "source_hashes": hashes, "training_manifest_sha256": digest(training / "manifest.json"),
          "target_cases_sha256": digest(target / "cases.json"),
          "reference_sha256": digest(target / "decisions.json"),
          "artifact_hashes": {p.name: digest(p) for p in output.glob("*.json")}})
    check_source(training)
    assert all(digest(p) == h for p, h in hashes.items())
    write(output / "closeout.json", {"sources_and_training_unchanged": True,
          "all_statuses_correct": True, "all_ready_plans_replayed": True,
          "no_target_learning_or_cache_growth": True})
    print(json.dumps(summary), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("source", "transfer"), required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--training", type=Path)
    args = p.parse_args()
    if args.phase == "source":
        source(args.output)
    elif args.training is None:
        p.error("transfer requires --training")
    else:
        transfer(args.training, args.output)


if __name__ == "__main__":
    main()
