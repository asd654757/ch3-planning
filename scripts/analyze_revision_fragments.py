"""Replay fragment certificates and finite audit results without model calls."""
import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from ch3.context_memory.cases import load_cases
from ch3.context_memory.core import evaluate
from ch3.revision.evaluation import valid_suffix
from ch3.revision.fragments import (Atom, Oracle, Pattern, VerifiedBank, bindings,
    contract, extract, instantiate, lift, structural_conflict)
from ch3.revision.history import Event, Snapshot, State, decide
from scripts.benchmark_revision_recovery_pilot import write


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def patterns(path):
    return [Pattern(tuple(Atom(**a) for a in p["atoms"]),
                    tuple(tuple(v) for v in p["variables"]), p["horizon"])
            for p in json.loads(path.read_text())]


def canonical(value):
    return json.dumps(value, sort_keys=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    archive = args.input
    manifest = json.loads((archive/"manifest.json").read_text())
    for source, expected in manifest["source_hashes"].items():
        assert digest(source) == expected, source
        assert digest(archive/"sources"/source) == expected, source
    for name in ("cases", "patterns", "certificates", "grounded_patterns", "grounded_certificates"):
        assert digest(archive/f"{name}.json") == manifest[f"{name}_sha256"]
    mining_oracle = Oracle()
    rejected_sources = 0
    replayed_grounded, replayed_lifted = set(), set()
    cache = {}
    for item in json.loads((archive/"mining.json").read_text()):
        snapshots = []
        for source in item["sources"]:
            path = Path(source["path"])
            if path not in cache:
                assert digest(path) == manifest["source_results_sha256"]
                assert digest(path.parent/"cases.json") == manifest["source_cases_sha256"]
                cache[path] = (json.loads(path.read_text()),
                    {c["id"]: c["snapshot"] for c in load_cases(path.parent/"cases.json")})
            raw, old_cases = cache[path]
            row = next(r for r in raw if r["id"] == source["case_id"] and r["method"] == source["method"])
            attempt = row["attempts"][source["attempt"]-1]
            assert digest(path) == source["results_sha256"]
            assert sha256(attempt["content"].encode()).hexdigest() == source["content_sha256"]
            snapshot = old_cases[source["case_id"]]
            _, rejection = evaluate(snapshot, attempt["content"])
            assert rejection is not None and rejection == attempt["rejection"]
            snapshots.append(snapshot)
            rejected_sources += 1
        assert len({s.digest for s in snapshots}) == 1
        produced = []
        for reverse in (False, True):
            core = extract(snapshots[0], mining_oracle, reverse=reverse)
            if core is not None:
                lifted_pattern = lift(core)
                replayed_grounded.add(core.key)
                replayed_lifted.add(lifted_pattern.key)
                produced.append({"grounded_key": core.key, "lifted_key": lifted_pattern.key,
                    "deletion_order": "reverse" if reverse else "forward"})
        assert produced == item["produced"]
    assert replayed_grounded == {p.key for p in patterns(archive/"grounded_patterns.json")}
    assert replayed_lifted == {p.key for p in patterns(archive/"patterns.json")}
    oracle = Oracle()
    grounded = VerifiedBank(patterns(archive/"grounded_patterns.json"), oracle)
    lifted = VerifiedBank(patterns(archive/"patterns.json"), oracle)
    deletion_plans = 0
    for bank, name in ((grounded, "grounded_certificates"), (lifted, "certificates")):
        saved = json.loads((archive/f"{name}.json").read_text())
        assert canonical(bank.certificates) == canonical(saved)
        for pattern, cert in zip(bank.patterns, saved):
            bound = set(instantiate(pattern, bindings(pattern)[0]))
            for witness in cert["deletion_witnesses_first_binding"]:
                remaining = bound-{Atom(**witness["removed_atom"])}
                task = contract(remaining)
                snapshot = Snapshot(task, State(**witness["state"]), (), "witness-replay").validate()
                assert valid_suffix(snapshot, task, tuple(Event(**e) for e in witness["plan"]))
                deletion_plans += 1
    cases = load_cases(archive/"cases.json")
    rows = json.loads((archive/"results.json").read_text())
    assert len(cases) == len(rows) == len({r["id"] for r in rows})
    summary = json.loads((archive/"summary.json").read_text())
    counts = Counter()
    pair_counts = Counter()
    pattern_hits = Counter()
    for case, row in zip(cases, rows):
        assert case["id"] == row["id"] and case["group"] == row["group"]
        snapshot = case["snapshot"]
        expected = decide(snapshot, snapshot.contract, 8).status
        assert row["reference_status"] == expected
        hits = {"recompute": False, "grounded_core": grounded.match(snapshot),
                "lifted_core": lifted.match(snapshot), "handwritten_rules": structural_conflict(snapshot)}
        for method, hit in hits.items():
            saved = row["methods"][method]
            assert saved["hit"] == bool(hit)
            assert saved["status"] == expected
            assert not hit or expected == "conflict"
            counts[method] += bool(hit)
            if isinstance(hit, dict):
                assert saved["pattern"] == hit["pattern"] and saved["binding"] == hit["binding"]
        if hits["lifted_core"]:
            pattern_hits[hits["lifted_core"]["pattern"]] += 1
        pair_counts["lifted_only_vs_grounded"] += bool(hits["lifted_core"] and not hits["grounded_core"])
        pair_counts["grounded_only_vs_lifted"] += bool(hits["grounded_core"] and not hits["lifted_core"])
        pair_counts["lifted_only_vs_rules"] += bool(hits["lifted_core"] and not hits["handwritten_rules"])
        pair_counts["rules_only_vs_lifted"] += bool(hits["handwritten_rules"] and not hits["lifted_core"])
    for method, hit_count in counts.items():
        stats = summary["methods"][method]
        assert stats["hits"] == hit_count
        assert stats["calls"] == len(rows)-hit_count
        assert stats["status_matches"] == len(rows) and stats["false_conflicts"] == 0
    result = {"archive_source_and_frozen_hashes_valid": True,
        "source_rejected_candidates_replayed": rejected_sources,
        "automatic_extraction_reproduced": True, "extraction_replay_calls": mining_oracle.calls,
        "certificates_reproduced": True, "recertification_oracle_calls": oracle.calls,
        "deletion_witness_plans_replayed": deletion_plans,
        "case_statuses_and_all_method_hits_replayed": len(rows), "false_conflicts": 0,
        "paired_coverage": dict(pair_counts), "lifted_pattern_hits": dict(pattern_hits),
        "original_results_sha256": digest(archive/"results.json"),
        "additional_model_calls": 0,
        "scope": "independent result replay with same finite domain oracle, not independent physical evidence"}
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/"analysis.json", result)
    write(args.output/"manifest.json", {"analysis_source_sha256": digest(__file__),
        "input": str(archive), "additional_model_calls": 0})
    print(json.dumps(result))


if __name__ == "__main__":
    main()
