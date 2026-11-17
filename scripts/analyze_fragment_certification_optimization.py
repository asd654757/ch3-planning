"""Independent accounting of shared-search/compact-certificate optimization."""
import argparse
import json
from pathlib import Path
from statistics import median

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from scripts.analyze_fragment_three_objects import conditions, matches
from scripts.benchmark_fragment_three_objects import ROOT, check, digest, load, read, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    folders = {name: ROOT / name for name in (
        "fragment_batch_equivalence_20261116_v1", "fragment_batch_source_20261116_v1",
        "fragment_batch_validation_20261116_v1", "fragment_compact_equivalence_20261116_v1",
        "fragment_compact_source_20261116_v1", "fragment_compact_validation_20261116_v1",
        "fragment_three_fixtures_20261116_v2", "fragment_three_source_20261116_v1")}
    for folder in folders.values():
        check(folder)
    source = folders["fragment_compact_source_20261116_v1"]
    target = folders["fragment_compact_validation_20261116_v1"]
    original = folders["fragment_three_source_20261116_v1"]
    fixtures = folders["fragment_three_fixtures_20261116_v2"]
    source_times, target_times = read(source / "timings.json"), read(target / "timings.json")
    summary = read(target / "summary.json")
    old_by_key = {c["key"]: c for c in read(original / "learned_certificates.json")}
    certs = {name: read(source / f"{name}_certificates.json") for name in ("fixed", "learned")}
    certified_states = {}
    for name in certs:
        count = 0
        for cert in certs[name]:
            old = old_by_key[cert["key"]]
            assert len(cert["bindings"]) == len(old["bindings"])
            for binding, previous in zip(cert["bindings"], old["bindings"]):
                assert binding["binding"] == previous["binding"]
                assert binding["atoms"] == previous["atoms"]
                assert binding["bad_state_mask"] == previous["bad_state_mask"]
                assert len(binding["minimum_actions"]) == len(previous["states"]) == 34
                count += len(binding["minimum_actions"])
        certified_states[name] = count
    paired, work = [], {}
    for repeat in range(3):
        pair = {"repeat": repeat}
        for name in certs:
            s = next(r for r in source_times if r["repeat"] == repeat and r["method"] == name)
            t = next(r for r in target_times if r["repeat"] == repeat and r["method"] == name)
            total = s["preparation_ms"] + t["target_preparation_ms"] + t["query_ms"]
            assert abs(total - t["total_algorithm_ms_estimate"]) < 1e-8
            assert t["ready_plans_replayed"] == 336
            pair[name + "_total_ms"] = total
            if repeat == 0:
                loads = t["load_recertification_oracles"]
                work[name] = {
                    "full_task_source_searches": s["source_backend_calls"],
                    "full_task_target_searches": t["target_backend_calls"],
                    "initial_profile_searches": s["initial_oracle"]["profile_searches"],
                    "learning_profile_searches": s["learning_oracle"]["profile_searches"],
                    "reload_profile_searches": sum(v["profile_searches"] for v in loads),
                    "initial_logical_states": s["initial_oracle"]["total_logical_state_queries"],
                    "learning_logical_states": s["learning_oracle"]["total_logical_state_queries"],
                    "reload_logical_states": sum(v["total_logical_state_queries"] for v in loads),
                }
        pair["saved_ms"] = pair["fixed_total_ms"] - pair["learned_total_ms"]
        paired.append(pair)
    for name in certs:
        value = median(row[name + "_total_ms"] for row in paired)
        assert value == summary["methods"][name]["median_total_algorithm_ms_estimate"]
    d = build_compact_domain()
    lower = IndependentActionBound(d)
    fixed_keys = {c["key"] for c in certs["fixed"]}
    new = [c for c in certs["learned"] if c["key"] not in fixed_keys]
    three_keys = {c["key"] for c in new if sum(sort == "object" for _, sort in c["pattern"]["variables"]) == 3}
    guard_fixed = conditions(d, certs["fixed"])
    guard_all = conditions(d, certs["learned"])
    guard_without_three = conditions(d, [c for c in certs["learned"] if c["key"] not in three_keys])
    reference = read(ROOT / "fragment_three_validation_20261116_v1/reference.json")
    memo = {tuple(v["key"]) for v in read(source / "shared_memo.json")}
    extra = necessary = 0
    for case in load(d, fixtures / "target_cases.json"):
        s = case["snapshot"]
        mask, state = d.features(s)
        assert (mask, state) not in memo
        if lower.match(s, 8):
            assert reference[case["id"]] == "conflict"
            continue
        a, b = matches(guard_fixed, mask, state), matches(guard_all, mask, state)
        assert not b or reference[case["id"]] == "conflict"
        if b and not a:
            extra += 1
            necessary += not matches(guard_without_three, mask, state)
    assert extra == 66 and necessary == 47
    sizes = {name: {"original_full_bytes": (original / f"{name}_certificates.json").stat().st_size,
                    "batch_full_bytes": (ROOT / "fragment_batch_source_20261116_v1" / f"{name}_certificates.json").stat().st_size,
                    "compact_bytes": (source / f"{name}_certificates.json").stat().st_size} for name in certs}
    metrics = {"paired_full_costs": paired, "profile_and_full_task_work": work,
               "all_binding_state_results_still_certified": certified_states,
               "extra_coverage_preserved": extra, "necessary_three_object_coverage_preserved": necessary,
               "certificate_file_sizes": sizes,
               "full_cost_reduction_percent": summary["full_cost_reduction_percent"],
               "query_reduction_percent": summary["query_reduction_percent"],
               "all_three_paired_totals_faster": all(r["saved_ms"] > 0 for r in paired),
               "minimum_paired_saving_ms": min(r["saved_ms"] for r in paired),
               "model_calls": 0, "physical_execution": False}
    write(args.output / "metrics.json", metrics)
    write(args.output / "verification.json", {"all_frozen_hashes_verified": True,
          "state_coverage_and_masks_identical_to_original": True,
          "timing_total_arithmetic_recomputed": True,
          "extra_and_three_object_coverage_independently_recomputed": True})
    write(args.output / "manifest.json", {"analysis_script_sha256": digest(__file__),
          "input_manifests": {str(p / "manifest.json"): digest(p / "manifest.json") for p in folders.values()},
          "artifact_hashes": {p.name: digest(p) for p in args.output.glob("*.json")}})
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
