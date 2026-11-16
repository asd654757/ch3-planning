"""Independent source replay and causal coverage accounting for three objects."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path

from ch3.revision.fragment_domain_adapter import build_domain, IndependentActionBound
from ch3.revision.fragments import Atom
from scripts.benchmark_fragment_three_objects import (
    ROOT, check, load, read, write, digest, structural_signature, verify_plan,
)


def conditions(d, certificates):
    result = [[] for _ in d.states]
    for c in certificates:
        for binding in c["bindings"]:
            bits = d.regions.task_mask(tuple(Atom(**a) for a in binding["atoms"]))
            for i in range(len(d.states)):
                if binding["bad_state_mask"] & (1 << i):
                    result[i].append((bits, c["key"]))
    return result


def matches(guards, mask, state):
    return {key for bits, key in guards[state] if bits & mask == bits}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    fixtures = ROOT / "fragment_three_fixtures_20261116_v2"
    source = ROOT / "fragment_three_source_20261116_v1"
    target = ROOT / "fragment_three_validation_20261116_v1"
    for folder in (fixtures, source, target):
        check(folder)
    d = build_domain()
    sources = load(d, fixtures / "source_cases.json")
    cases = load(d, fixtures / "target_cases.json")
    all_signatures = [structural_signature(d, c["snapshot"]) for c in sources + cases]
    assert len(set(all_signatures)) == len(all_signatures) == 1152
    source_map = {c["id"]: c for c in sources}
    source_trace = read(source / "learned_source_trace.json")
    source_reference = {}
    for case in sources:
        s = case["snapshot"]
        decision = d.model.decide(s, s.contract, 8)
        verify_plan(d, s, decision)
        source_reference[case["id"]] = decision.status
    for name in ("fixed", "learned"):
        assert all(r["status"] == source_reference[r["id"]]
                   for r in read(source / f"{name}_source_trace.json"))
    core_proofs = []
    for row in source_trace:
        if row["new_pattern"] is None:
            continue
        s = source_map[row["id"]]["snapshot"]
        oracle = d.old.Oracle()
        core = d.regions.minimize(s, oracle)
        pattern = d.regions.canonical(core)
        assert pattern.key == row["new_pattern"]
        deletion = []
        for a in core:
            reduced = d.model.Snapshot(d.old.contract(set(core) - {a}), s.state, (), "three-core-replay")
            result = d.model.decide(reduced, reduced.contract, 8)
            assert result.status == "ready"
            verify_plan(d, reduced, result)
            deletion.append({"removed_atom": asdict(a), "plan_length": len(result.plan)})
        core_proofs.append({"source_id": row["id"], "key": pattern.key, "core": [asdict(a) for a in core],
                            "state": asdict(s.state), "pattern": asdict(pattern), "deletion_witnesses": deletion})
    fixed_certs = read(source / "fixed_certificates.json")
    learned_certs = read(source / "learned_certificates.json")
    old_keys = {c["key"] for c in fixed_certs}
    new = [c for c in learned_certs if c["key"] not in old_keys]
    three_keys = {c["key"] for c in new if sum(t == "object" for _, t in c["pattern"]["variables"]) == 3}
    new_other = [c for c in new if c["key"] not in three_keys]
    fixed_guards = conditions(d, fixed_certs)
    without_three = conditions(d, fixed_certs + new_other)
    all_guards = conditions(d, learned_certs)
    lower = IndependentActionBound(d)
    reference = read(target / "reference.json")
    memo = {tuple(r["key"]) for r in read(source / "shared_memo.json")}
    extra = []
    essential_three = 0
    origin_counts = {"fixed": Counter(), "learned": Counter()}
    for case in cases:
        s = case["snapshot"]
        mask, state_index = d.features(s)
        assert (mask, state_index) not in memo, "canonical exclusion should remove exact feature overlap"
        if lower.match(s, 8):
            assert reference[case["id"]] == "conflict"
            origin_counts["fixed"]["bound"] += 1
            origin_counts["learned"]["bound"] += 1
            continue
        base = matches(fixed_guards, mask, state_index)
        learned = matches(all_guards, mask, state_index)
        for name, hit in (("fixed", base), ("learned", learned)):
            origin_counts[name]["certified_core" if hit else "backend"] += 1
            assert not hit or reference[case["id"]] == "conflict"
        if learned and not base:
            needs_three = not matches(without_three, mask, state_index)
            essential_three += needs_three
            extra.append({"id": case["id"], "requires_new_three_object_core": needs_three,
                          "supporting_new_keys": sorted(learned - old_keys),
                          "snapshot": s, "reference": reference[case["id"]]})
    summary = read(target / "summary.json")
    assert len(extra) == summary["additional_backend_calls_avoided"]
    for name in origin_counts:
        assert origin_counts[name]["backend"] == summary["methods"][name]["target_backend_calls"]
    a, b = summary["methods"]["fixed"], summary["methods"]["learned"]
    totals = {}
    for name, metrics in summary["methods"].items():
        totals[name] = sum(metrics[k] for k in ("target_backend_calls", "source_backend_calls",
                           "initial_certification_calls", "learning_oracle_calls", "reload_certification_calls"))
    metrics = {"target_full_task_reduction_percent": (a["target_backend_calls"] - b["target_backend_calls"]) / a["target_backend_calls"] * 100,
               "query_reduction_percent": (a["median_query_ms"] - b["median_query_ms"]) / a["median_query_ms"] * 100,
               "total_algorithm_time_ratio_learned_over_fixed": b["total_algorithm_ms_estimate"] / a["total_algorithm_ms_estimate"],
               "all_core_and_backend_calls": totals,
               "new_patterns": len(new), "new_three_object_patterns": len(three_keys),
               "extra_target_coverage": len(extra), "essential_new_three_object_coverage": essential_three,
               "coverage_origins_recomputed_from_certificates": origin_counts,
               "source_and_target_canonical_overlap": 0, "exact_cache_target_overlap": 0,
               "source_statuses_independently_replayed": len(sources), "core_deletion_proofs": len(core_proofs),
               "model_calls": 0, "physical_execution": False}
    write(args.output / "metrics.json", metrics)
    write(args.output / "source_core_proofs.json", core_proofs)
    write(args.output / "new_coverage_by_mechanism.json", extra)
    write(args.output / "verification.json", {"all_hashes_verified": True,
          "all_source_statuses_match_full_backend": True, "all_signatures_independently_recomputed": True,
          "all_source_cores_and_deletion_witnesses_replayed": True,
          "all_target_coverage_recomputed_from_certificate_masks": True})
    write(args.output / "manifest.json", {"analysis_script_sha256": digest(__file__),
          "input_manifests": {str(f / "manifest.json"): digest(f / "manifest.json") for f in (fixtures, source, target)},
          "artifact_hashes": {f.name: digest(f) for f in args.output.glob("*.json")}})
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
