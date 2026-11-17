"""Independent attribution of certified transfer beyond state-bound nogoods."""
import argparse
from collections import Counter
from math import isclose
from pathlib import Path
from statistics import median
import json

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_state_cache import StateBoundBank
from scripts.analyze_fragment_execution import decode
from scripts.analyze_fragment_three_objects import conditions
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write

COMPARISON = ROOT / "fragment_state_cache_comparison_20261117_v1"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    check(COMPARISON)
    args.output.mkdir(parents=True, exist_ok=False)
    d = build_compact_domain()
    methods = ("fixed", "state_cache", "full")
    records = {name: read(COMPARISON / f"{name}_decisions.json") for name in methods}
    traces = read(COMPARISON / "semantic_trace.json")
    cached = read(COMPARISON / "source_r0_state_cache.json")
    full = read(COMPARISON / "source_r0_full.json")
    fixed_bank = d.regions.RegionBank(cached["certificates"])
    cache_bank = StateBoundBank(d, fixed_bank, cached["failures"])
    full_bank = d.regions.RegionBank(full["certificates"])
    old_keys = {c["key"] for c in cached["certificates"]}
    new = [c for c in full["certificates"] if c["key"] not in old_keys]
    three_keys = {c["key"] for c in new if sum(t == "object" for _, t in c["pattern"]["variables"]) == 3}
    all_guards = conditions(d, full["certificates"])
    without_three = conditions(d, [c for c in full["certificates"] if c["key"] not in three_keys])
    source_core_masks = {(bits, p["key"]) for (_, bits), p in cache_bank.entries.items()}
    new_union = {(bits, key) for entries in all_guards for bits, key in entries if key not in old_keys}
    extra, cache_extra, full_fixed_extra = [], [], []
    ungated_false = {"full_bindings": [], "source_symmetry_bindings": []}
    index = ready_plans = eligible_ready = 0
    for row in traces:
        for j, step in enumerate(row["decisions"]):
            snapshot = decode(d, step["snapshot"])
            decision = step["decision"]
            gold = d.model.decide(snapshot, snapshot.contract, 8)
            assert decision["status"] == gold.status
            if gold.status == "ready":
                # Reference planner also confirms existence; replay the actual suffix.
                state = snapshot.state
                plan = tuple(d.model.Event(**e) for e in decision["plan"])
                assert len(plan) <= 8
                for k, event in enumerate(plan, 1):
                    assert event.seq == snapshot.cursor+k
                    state = next(ns for ns, e in d.model.successors(state, event.seq)
                        if (e.seq, e.kind, e.object, e.target) == (event.seq, event.kind, event.object, event.target))
                assert all(d.model.monitor(snapshot.contract, state, (*h, *plan)) == (frozenset(), frozenset())
                           for h in d.model.possible_histories(snapshot.history))
                ready_plans += 1
            else:
                assert not decision["plan"]
            mask, state_index = d.features(snapshot)
            banks = {"fixed": fixed_bank, "state_cache": cache_bank, "full": full_bank}
            for name in methods:
                r = records[name][index]
                assert r["status"] == gold.status and r["cursor"] == snapshot.cursor
                if r["origin"] == "certified_core":
                    assert banks[name].evidence(snapshot) == r["proof"]
                elif r["origin"] == "backend":
                    assert banks[name].evidence(snapshot) is None
            if records["state_cache"][index]["origin"] == "certified_core":
                assert records["full"][index]["origin"] == "certified_core"
            location = {"episode": row["episode"], "revision": row["revision"],
                "decision_index_within_revision": j, "stage": "task_revision" if j == 0 else "post_action_recovery"}
            if records["fixed"][index]["origin"] == "backend" and records["state_cache"][index]["origin"] == "certified_core":
                cache_extra.append(location)
            if records["fixed"][index]["origin"] == "backend" and records["full"][index]["origin"] == "certified_core":
                full_fixed_extra.append(location)
            if records["state_cache"][index]["origin"] == "backend" and records["full"][index]["origin"] == "certified_core":
                matches = [(bits, key) for bits, key in all_guards[state_index] if bits & mask == bits and key not in old_keys]
                same_ground_core = [(bits, key) for bits, key in matches if (bits, key) in source_core_masks]
                extra.append({**location, "snapshot": snapshot,
                    "explanation": "same_grounded_core_at_additional_state" if same_ground_core
                        else "requires_additional_non_symmetry_target_binding_in_frozen_bank",
                    "requires_new_three_object_core": not any(bits & mask == bits for bits, _ in without_three[state_index]),
                    "supporting_new_keys": sorted({key for _, key in matches}),
                    "same_grounded_core_witnesses": [{"required_mask": bits, "key": key} for bits, key in same_ground_core]})
            # Post-hoc diagnostic only: keep common suffix/lower-bound gates and
            # remove state guards from NEW learned cores. Never dispatch this arm.
            if records["full"][index]["origin"] == "backend" and gold.status == "ready":
                eligible_ready += 1
                if any(bits & mask == bits for bits, _ in new_union):
                    ungated_false["full_bindings"].append(location)
                if any(bits & mask == bits for bits, _ in source_core_masks):
                    ungated_false["source_symmetry_bindings"].append(location)
            index += 1
    assert all(len(records[name]) == index for name in methods)
    timings = read(COMPARISON / "timings.json")
    summary = read(COMPARISON / "summary.json")
    assert len(timings) == 30
    for name in methods:
        rows = [r for r in timings if r["method"] == name]
        assert len(rows) == 10
        for r in rows:
            assert isclose(r["online_total_ms"], r["preparation_ms"]+r["execution_loop_ms"], abs_tol=1e-9)
            assert isclose(r["total_algorithm_ms"], r["source_ms"]+r["online_total_ms"], abs_tol=1e-9)
            assert r["backend_calls"] == Counter(x["origin"] for x in records[name])["backend"]
            assert r["source_backend_calls"] == r["source_cache_size"] == 333
        for metric, expected in summary["methods"][name]["medians"].items():
            assert median(r[metric] for r in rows) == expected
    for control in ("fixed", "state_cache"):
        comp = summary["full_comparisons"][control]
        for metric, values in comp["paired_savings_ms"].items():
            actual = [next(r[metric] for r in timings if r["repeat"] == i and r["method"] == control)
                - next(r[metric] for r in timings if r["repeat"] == i and r["method"] == "full") for i in range(10)]
            assert actual == values and comp["paired_wins"][metric] == sum(x > 0 for x in actual)
    assert len(extra) == summary["full_comparisons"]["state_cache"]["backend_calls_saved"]
    assert len(full_fixed_extra) == summary["full_comparisons"]["fixed"]["backend_calls_saved"]
    metrics = {"independently_replayed_decisions": index, "independently_replayed_ready_suffixes": ready_plans,
        "state_cache_points": len(cache_bank.entries), "cache_extra_vs_fixed": len(cache_extra),
        "cache_extra_vs_fixed_by_stage": Counter(r["stage"] for r in cache_extra),
        "full_extra_vs_cache": len(extra), "full_extra_vs_cache_by_stage": Counter(r["stage"] for r in extra),
        "full_extra_vs_cache_by_explanation": Counter(r["explanation"] for r in extra),
        "full_extra_vs_cache_requiring_new_three_object_core": sum(r["requires_new_three_object_core"] for r in extra),
        "unguarded_diagnostic": {"ready_decisions_reaching_core_check": eligible_ready,
            "false_conflicts_full_bindings": len(ungated_false["full_bindings"]),
            "false_conflicts_source_symmetry_bindings": len(ungated_false["source_symmetry_bindings"]),
            "status": "post_hoc_diagnostic_no_actions_dispatched"},
        "all_timing_arithmetic_and_pairwise_wins_verified": True, "model_calls": 0, "physical_execution": False}
    write(args.output / "metrics.json", metrics)
    write(args.output / "extra_coverage.json", extra)
    write(args.output / "unguarded_diagnostic.json", ungated_false)
    close(args.output, {str(Path(__file__)): digest(__file__),
        "scripts/analyze_fragment_execution.py": digest("scripts/analyze_fragment_execution.py"),
        "scripts/analyze_fragment_three_objects.py": digest("scripts/analyze_fragment_three_objects.py")},
        {str(COMPARISON / "manifest.json"): digest(COMPARISON / "manifest.json")})
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
