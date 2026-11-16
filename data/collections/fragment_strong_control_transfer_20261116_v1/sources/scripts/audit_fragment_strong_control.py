"""Retrospective failure-core audit after giving the known budget core to rules.

Source learning and target evaluation are separate invocations. No new domain,
planner, model requests, or physical robot trials are introduced.
"""
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
from ch3.revision.fragments import Atom, Oracle, Pattern
from ch3.revision.fragment_regions import (
    RegionBank, canonical, expert_patterns, features, minimize,
)
from ch3.revision.history import decide
from scripts.benchmark_revision_fragments import signature, temporal_probes
from scripts.benchmark_revision_recovery_pilot import write


ROOT = Path("data/collections")
PRIOR = ROOT / "fragment_regions_development_20261116_v2"
SOURCE = ROOT / "revision_fragments_20261116_v1/cases.json"
PROTOCOL = Path("docs/fragment_strong_control_protocol_20261116.md")


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def read_pattern(raw):
    return Pattern(tuple(Atom(**a) for a in raw["atoms"]),
                   tuple(tuple(v) for v in raw["variables"]), raw["horizon"])


def cache_key(snapshot):
    if snapshot.history:
        return None
    return repr(features(snapshot))


def freeze_sources(output):
    sources = [Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
               Path("ch3/revision/fragment_regions.py"), Path("ch3/revision/fragments.py"),
               Path("ch3/revision/history.py"), Path("ch3/context_memory/cases.py"),
               Path("scripts/benchmark_revision_fragments.py"),
               Path("scripts/benchmark_revision_recovery_pilot.py")]
    hashes = {str(s): digest(s) for s in sources}
    for source in sources:
        target = output / "sources" / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return hashes


def train(output):
    output.mkdir(parents=True, exist_ok=False)
    hashes = freeze_sources(output)
    prior_manifest = json.loads((PRIOR / "manifest.json").read_text())
    for name, expected in prior_manifest["frozen_artifact_hashes"].items():
        assert digest(PRIOR / name) == expected, name
    budget_patterns = [read_pattern(p) for p in json.loads((PRIOR / "learned_patterns.json").read_text())]
    assert len(budget_patterns) == 1, "the protocol supplies the single already discovered budget core"
    seed_patterns = [read_pattern(p) for p in json.loads((PRIOR / "seed_patterns.json").read_text())]
    # Supply ALL previously available knowledge. The old seed cores can be
    # semantically redundant through common feature closure while still having
    # different compiled conjunctions; table equality alone cannot prove that.
    patterns = [*seed_patterns, *budget_patterns]
    start = perf_counter()
    cases = load_cases(SOURCE)
    random.Random(11661).shuffle(cases)
    source_load_ms = (perf_counter() - start) * 1000
    oracle = Oracle()
    start = perf_counter()
    fixed = RegionBank.verified([*expert_patterns(), *patterns], oracle)
    fixed_build_ms = (perf_counter() - start) * 1000
    old_learned = RegionBank(json.loads((PRIOR / "learned_certificates.json").read_text()))
    condition_equal = fixed.conditions == old_learned.conditions
    assert condition_equal, "audit any unexpected compiled-condition difference first"
    write(output / "fixed_certificates.json", fixed.certificates)
    baseline_calls = learner_calls = 0
    baseline_source_ms = 0.0
    active = fixed
    learned, trace, memo, witnesses = {}, [], {}, []
    began = perf_counter()
    for index, case in enumerate(cases):
        snapshot = case["snapshot"]
        baseline_started = perf_counter()
        fixed_hit = fixed.match(snapshot)
        status = "conflict" if fixed_hit else decide(snapshot, snapshot.contract, 8).status
        baseline_calls += not fixed_hit
        key = cache_key(snapshot)
        if not fixed_hit and key is not None:
            if key in memo:
                assert memo[key] == status, "static feature equivalence must preserve status"
            memo[key] = status
        baseline_source_ms += (perf_counter() - baseline_started) * 1000
        learner_hit = active.match(snapshot)
        learner_calls += not learner_hit
        new_key = None
        if not learner_hit and status == "conflict":
            core = minimize(snapshot, oracle)
            if core:
                pattern = canonical(core)
                assert pattern.key not in learned, "certified source core should already match"
                before = oracle.calls
                new_bank = RegionBank.verified([pattern], oracle)
                active = RegionBank.combine(active, new_bank)
                assert active.match(snapshot)
                learned[pattern.key] = pattern
                new_key = pattern.key
                deletion = []
                for atom in core:
                    counterexample = oracle.solve(set(core) - {atom}, snapshot.state, 8)
                    assert counterexample.status == "ready"
                    deletion.append({"removed_atom": asdict(atom), "status": counterexample.status,
                                     "plan": [asdict(e) for e in counterexample.plan]})
                witnesses.append({"source_id": case["id"], "source_state": asdict(snapshot.state),
                                  "pattern": asdict(pattern), "core": [asdict(a) for a in core],
                                  "certification_and_deletion_oracle_calls": oracle.calls - before,
                                  "deletion_witnesses": deletion})
                print(json.dumps({"at": index, "new_pattern": asdict(pattern)}), flush=True)
        trace.append({"id": case["id"], "snapshot_sha256": snapshot.digest,
                      "fixed_hit": fixed_hit, "learner_hit_before_update": learner_hit,
                      "reference_status": status, "new_pattern": new_key})
        if (index + 1) % 1000 == 0:
            print(json.dumps({"completed_sources": index + 1, "learned": len(learned)}), flush=True)
    total_source_ms = (perf_counter() - began) * 1000
    write(output / "learned_patterns.json", list(learned.values()))
    write(output / "learned_certificates.json", active.certificates)
    write(output / "source_trace.json", trace)
    write(output / "source_order.json", [case["id"] for case in cases])
    write(output / "source_memo.json", memo)
    write(output / "deletion_witnesses.json", witnesses)
    summary = {"n": len(cases), "compiled_fixed_equals_previous_learned": condition_equal,
               "prior_seed_patterns_also_supplied_to_fixed": len(seed_patterns),
               "new_patterns": len(learned), "baseline_actual_backend_calls": baseline_calls,
               "learner_backend_requests_under_shared_observations": learner_calls,
               "core_oracle_calls_including_fixed_certification": oracle.calls,
               "source_cache_keys": len(memo), "source_load_ms": source_load_ms,
               "fixed_build_ms": fixed_build_ms, "baseline_source_processing_ms": baseline_source_ms,
               "whole_shared_source_processing_ms": total_source_ms,
               "source_statuses": Counter(row["reference_status"] for row in trace),
               "cost_scope": "whole source cost includes fixed baseline observations even after learner hits; conservative staged-audit cost, not optimized online latency",
               "target_files_read_during_learning": False, "model_calls": 0,
               "physical_execution": False, "retrospective": True}
    write(output / "summary.json", summary)
    artifacts = {p.name: digest(p) for p in output.glob("*.json")}
    write(output / "manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
          "source_hashes": hashes, "artifact_hashes": artifacts,
          "input_hashes": {str(SOURCE): digest(SOURCE),
                           str(PRIOR / "manifest.json"): digest(PRIOR / "manifest.json")},
          "target_files_read_during_learning": False})
    assert all(digest(s) == h for s, h in hashes.items())
    print(json.dumps(summary), flush=True)


def check_frozen(training):
    manifest = json.loads((training / "manifest.json").read_text())
    for name, expected in manifest["artifact_hashes"].items():
        assert digest(training / name) == expected, name
    for source, expected in manifest["source_hashes"].items():
        assert digest(source) == expected, source
    for source, expected in manifest["input_hashes"].items():
        assert digest(source) == expected, source
    return manifest


def evaluate(training, output):
    check_frozen(training)
    output.mkdir(parents=True, exist_ok=False)
    hashes = freeze_sources(output)
    banks, loads = {}, {}
    for name in ("fixed", "learned"):
        start = perf_counter()
        banks[name] = RegionBank(json.loads((training / f"{name}_certificates.json").read_text()))
        loads[name] = (perf_counter() - start) * 1000
    memo = json.loads((training / "source_memo.json").read_text())
    seen_paths = [SOURCE, ROOT / "context_memory_development_20261113_v1/cases.json",
                  ROOT / "context_memory_validation_20261116_v2/cases.json"]
    seen = {signature(c["snapshot"]) for p in seen_paths for c in load_cases(p)}
    target = ROOT / "fragment_regions_validation_20261116_v1"
    target_manifest = json.loads((target / "manifest.json").read_text())
    assert digest(target / "cases.json") == target_manifest["frozen_artifact_hashes"]["cases.json"]
    cases = [c for c in load_cases(target / "cases.json") if signature(c["snapshot"]) not in seen]
    reference = {r["id"]: r["status"] for r in json.loads((target / "decisions.json").read_text())}
    check_frozen(training)
    coverage = {name: Counter() for name in banks}
    additional, memo_overlaps = [], 0
    for i, case in enumerate(cases):
        s = case["snapshot"]
        key = cache_key(s)
        memo_hit = key is not None and key in memo
        memo_overlaps += memo_hit
        flags = {}
        for name, bank in banks.items():
            hit = bank.match(s)
            flags[name] = hit
            coverage[name]["rule_hits"] += hit
            coverage[name]["cache_hits_after_rule"] += not hit and memo_hit
            coverage[name]["backend_calls"] += not hit and not memo_hit
            if hit:
                assert reference[case["id"]] == "conflict"
            elif memo_hit:
                assert memo[key] == reference[case["id"]]
        assert not flags["fixed"] or flags["learned"], "cannot lose fixed coverage"
        if flags["learned"] and not flags["fixed"] and not memo_hit:
            result = decide(s, s.contract, 8)
            assert result.status == "conflict", "new rejection failed independent backend replay"
            additional.append({"id": case["id"], "evidence": banks["learned"].evidence(s),
                               "independent_backend_status": result.status})
        if (i + 1) % 10000 == 0:
            print(json.dumps({"coverage_completed": i + 1, "additional": len(additional)}), flush=True)
    boundaries = []
    for case in temporal_probes():
        s = case["snapshot"]
        status = decide(s, s.contract, 8).status
        row = {"id": case["id"], "reference": status}
        for name, bank in banks.items():
            hit = bank.match(s)
            assert not hit or status == "conflict"
            row[name] = hit
        boundaries.append(row)
    write(output / "additional_coverage.json", additional)
    write(output / "boundary_checks.json", boundaries)
    timings = []
    if additional:
        for repeat in range(3):
            indices = list(range(len(cases)))
            random.Random(11671 + repeat).shuffle(indices)
            for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
                calls = hits = cache_hits = 0
                statuses = []
                start = perf_counter()
                for index in indices:
                    s = cases[index]["snapshot"]
                    hit = banks[name].match(s)
                    if hit:
                        status = "conflict"
                        hits += 1
                    else:
                        key = cache_key(s)
                        if key is not None and key in memo:
                            status = memo[key]
                            cache_hits += 1
                        else:
                            status = decide(s, s.contract, 8).status
                            calls += 1
                    statuses.append(status)
                elapsed = (perf_counter() - start) * 1000
                assert all(status == reference[cases[i]["id"]] for i, status in zip(indices, statuses))
                row = {"repeat": repeat, "method": name, "wall_ms": elapsed,
                       "backend_calls": calls, "rule_hits": hits, "cache_hits": cache_hits}
                timings.append(row)
                write(output / "timings.json", timings)
                print(json.dumps(row), flush=True)
    training_summary = json.loads((training / "summary.json").read_text())
    preparation = {
        "fixed": training_summary["source_load_ms"] + training_summary["fixed_build_ms"]
                 + training_summary["baseline_source_processing_ms"] + loads["fixed"],
        "learned": training_summary["source_load_ms"] + training_summary["fixed_build_ms"]
                   + training_summary["whole_shared_source_processing_ms"] + loads["learned"],
    }
    timing_summary = {}
    for name in banks:
        values = [r["wall_ms"] for r in timings if r["method"] == name]
        if values:
            timing_summary[name] = {"median_query_ms": median(values),
                                    "preparation_ms": preparation[name],
                                    "median_total_ms": median(values) + preparation[name]}
    summary = {"n": len(cases), "coverage": coverage, "additional_backend_calls_avoided": len(additional),
               "source_cache_target_overlaps": memo_overlaps, "boundary_cases": len(boundaries),
               "false_rejections": 0, "timing": timing_summary,
               "timing_skipped_if_no_additional_coverage": not additional,
               "retrospective": True, "target_learning": False, "model_calls": 0,
               "physical_execution": False}
    write(output / "summary.json", summary)
    write(output / "manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
          "training": str(training), "training_manifest_sha256": digest(training / "manifest.json"),
          "source_hashes": hashes, "target_cases_sha256": digest(target / "cases.json"),
          "reference_sha256": digest(target / "decisions.json"),
          "artifact_hashes": {p.name: digest(p) for p in output.glob("*.json")}})
    check_frozen(training)
    assert all(digest(p) == h for p, h in hashes.items())
    write(output / "closeout.json", {"sources_unchanged": True, "training_unchanged": True,
          "all_rejections_checked": True, "all_new_rejections_independently_resolved": True,
          "no_target_learning": True})
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("source", "transfer"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training", type=Path)
    args = parser.parse_args()
    if args.phase == "source":
        train(args.output)
    else:
        if args.training is None:
            parser.error("transfer requires --training")
        evaluate(args.training, args.output)


if __name__ == "__main__":
    main()
