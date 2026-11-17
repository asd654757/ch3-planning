"""Independent original-BFS witness, partial-guard and representation audit."""
import argparse
from collections import Counter, defaultdict
from itertools import combinations, permutations
from math import isclose
from pathlib import Path
from statistics import mean, median
import json

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_partial_nogoods import PartialNogoodBank
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.fragments import Atom
from scripts.analyze_fragment_execution import decode
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write

COMPARISON = ROOT / "fragment_partial_nogood_comparison_20261117_v1"
ORIGINAL = ROOT / "fragment_three_source_20261116_v1"
PREVIOUS = ROOT / "fragment_state_cache_compiled_comparison_20261117_v1"


def atom_key(raw):
    return tuple(sorted(Atom(**a) for a in raw))


def completion_mask(d, guard):
    # Independent of the new module's guard_universe/mask construction.
    return sum(1 << i for i, s in enumerate(d.states) if all(getattr(s, o) == t for o, t in guard))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    out = parser.parse_args().output
    for folder in (COMPARISON, ORIGINAL, PREVIOUS):
        check(folder)
    out.mkdir(parents=True, exist_ok=False)
    d = build_compact_domain()
    methods = ("partial_source", "partial_complete", "full")
    payloads = {m: read(COMPARISON / f"source_r0_{m}.json") for m in methods}
    records = {m: read(COMPARISON / f"{m}_decisions.json") for m in methods}
    failures = read(COMPARISON / "full_source_failures.json")
    keys = {f["key"] for f in failures}
    old = {}
    declared_state_results = 0
    for certificate in read(ORIGINAL / "learned_certificates.json"):
        if certificate["key"] not in keys:
            continue
        for entry in certificate["bindings"]:
            bad = sum(1 << r["state_index"] for r in entry["states"] if r["status"] == "conflict")
            assert bad == entry["bad_state_mask"]
            key = certificate["key"], atom_key(entry["atoms"])
            if key in old:
                assert old[key]["bad"] == bad
            old[key] = {"bad": bad, "states": {r["state_index"]: r for r in entry["states"]}}
            declared_state_results += len(entry["states"])
    assert len(old) == 225
    full_state_results = 0
    for certificate in payloads["full"]["certificates"]:
        if certificate["key"] not in keys:
            continue
        for entry in certificate["bindings"]:
            expected = old[certificate["key"], atom_key(entry["atoms"])]
            assert entry["bad_state_mask"] == expected["bad"]
            for i, length in enumerate(entry["minimum_actions"]):
                original = expected["states"][i]
                status = "conflict" if length < 0 or length > 8 else "ready"
                assert status == original["status"]
                if status == "ready":
                    assert length == len(original["plan"])
                full_state_results += 1

    # Derive the SOURCE candidate language independently, including all target
    # permutations. Also build an untimed full-state/typed-binding coverage
    # control to separate binding breadth from deleting state facts.
    allowed = defaultdict(set)
    typed_points, symmetry_points = defaultdict(set), defaultdict(set)
    for failure in failures:
        source = failure["state"]
        for names in permutations(d.model.OBJECTS):
            rename = dict(zip(d.model.OBJECTS, names))
            for targets in permutations(d.model.TARGETS):
                places = {"hand": "hand", "": "", **dict(zip(d.model.TARGETS, targets))}
                atoms = tuple(sorted(Atom(a["kind"], rename.get(a["object"], ""), places[a["target"]])
                                     for a in failure["core"]))
                key = failure["key"], atoms
                facts = tuple(sorted((rename[o], places[source[o]]) for o in d.model.OBJECTS))
                for n in range(4):
                    allowed[key].update(combinations(facts, n))
                complete = completion_mask(d, facts)
                if complete and complete & ~old[key]["bad"] == 0:
                    bits = d.regions.task_mask(atoms)
                    for i in range(len(d.states)):
                        if complete & (1 << i):
                            typed_points[i].add(bits)
                            if places["table"] == "table":
                                symmetry_points[i].add(bits)
    guard_checks = []
    partial_tables = {}
    guards_by_mode = Counter()
    for method in methods[:-1]:
        table = {}
        for record in payloads[method]["nogoods"]:
            for entry in record["bindings"]:
                key = record["key"], atom_key(entry["atoms"])
                expected = old[key]
                covered = 0
                for raw in entry["guards"]:
                    guard = tuple(tuple(p) for p in raw)
                    assert len({o for o, _ in guard}) == len(guard)
                    if method == "partial_source":
                        assert guard in allowed[key]
                    mask = completion_mask(d, guard)
                    assert mask and mask & ~expected["bad"] == 0
                    covered |= mask
                    deletions = []
                    for i in range(len(guard)):
                        less = guard[:i]+guard[i+1:]
                        counterexamples = completion_mask(d, less) & ~expected["bad"]
                        assert counterexamples
                        witness = (counterexamples & -counterexamples).bit_length()-1
                        assert expected["states"][witness]["status"] == "ready"
                        deletions.append({"removed_fact": guard[i], "witness_state_index": witness,
                            "original_bfs_plan_length": len(expected["states"][witness]["plan"])})
                    guard_checks.append({"method": method, "key": record["key"], "atoms": entry["atoms"],
                        "guard": raw, "covered_state_mask": mask, "deletion_witnesses": deletions})
                    guards_by_mode[method] += 1
                assert covered == entry["bad_state_mask"]
                assert covered & ~expected["bad"] == 0
                if method == "partial_complete":
                    assert covered == expected["bad"]
                table[key] = covered
        assert set(table) == set(old)
        partial_tables[method] = table

    fixed = d.regions.RegionBank(payloads["partial_source"]["certificates"])
    banks = {"full": d.regions.RegionBank(payloads["full"]["certificates"])}
    for method in methods[:-1]:
        banks[method] = PartialNogoodBank(d, fixed, payloads[method]["failures"],
            mode=method.removeprefix("partial_"), expected_records=payloads[method]["nogoods"])
    assert banks["partial_complete"].conditions == banks["full"].conditions
    session = d.Session(banks["full"])
    session.freeze()
    bridge = build_fragment_runtime(d, session, IndependentActionBound(d))
    prior_fixed = read(PREVIOUS / "fixed_decisions.json")
    prior_state = read(PREVIOUS / "state_cache_decisions.json")
    index = ready = 0
    extra = []
    typed_backend = symmetry_backend = 0
    partial_state_gain = []
    for row in read(COMPARISON / "semantic_trace.json"):
        for j, step in enumerate(row["decisions"]):
            snapshot = decode(d, step["snapshot"])
            gold = d.model.decide(snapshot, snapshot.contract, 8)
            assert step["decision"]["status"] == gold.status
            if gold.status == "ready":
                plan = tuple(d.model.Event(**e) for e in step["decision"]["plan"])
                assert len(plan) <= 8 and bridge.valid_suffix(snapshot, snapshot.contract, plan)
                ready += 1
            else:
                assert not step["decision"]["plan"]
            mask, si = d.features(snapshot)
            for method in methods:
                r = records[method][index]
                assert r["status"] == gold.status and r["cursor"] == snapshot.cursor
                if r["origin"] == "certified_core":
                    assert banks[method].evidence(snapshot) == r["proof"]
                elif r["origin"] == "backend":
                    assert banks[method].evidence(snapshot) is None
            info = {"episode": row["episode"], "revision": row["revision"],
                "decision_index_within_revision": j, "stage": "task_revision" if j == 0 else "post_action_recovery"}
            if prior_fixed[index]["origin"] == "backend":
                typed_hit = any(bits & mask == bits for bits in typed_points[si])
                symmetry_hit = any(bits & mask == bits for bits in symmetry_points[si])
                assert (prior_state[index]["origin"] == "certified_core") == symmetry_hit
                symmetry_backend += not symmetry_hit
                typed_backend += not typed_hit
                assert not typed_hit or records["partial_source"][index]["origin"] == "certified_core"
                if not typed_hit and records["partial_source"][index]["origin"] == "certified_core":
                    partial_state_gain.append(info)
            if records["partial_source"][index]["origin"] == "backend" and records["full"][index]["origin"] == "certified_core":
                extra.append({**info, "snapshot": snapshot, "full_proof": records["full"][index]["proof"]})
            assert records["partial_complete"][index]["origin"] == records["full"][index]["origin"]
            index += 1
    assert index == 1618 and all(len(r) == index for r in records.values())
    assert symmetry_backend == 283
    summary = read(COMPARISON / "summary.json")
    timings = read(COMPARISON / "timings.json")
    assert len(timings) == 30
    for method in methods:
        rows = [r for r in timings if r["method"] == method]
        assert len(rows) == 10
        for r in rows:
            assert r["source_backend_calls"] == r["source_cache_size"] == 333
            assert isclose(r["source_ms"]+r["online_total_ms"], r["total_algorithm_ms"], abs_tol=1e-9)
            assert isclose(r["preparation_ms"]+r["execution_loop_ms"], r["online_total_ms"], abs_tol=1e-9)
            assert r["backend_calls"] == Counter(x["origin"] for x in records[method])["backend"]
        for field in summary["methods"][method]["medians"]:
            assert median(r[field] for r in rows) == summary["methods"][method]["medians"][field]
            assert mean(r[field] for r in rows) == summary["methods"][method]["means"][field]
    assert len(extra) == summary["full_comparisons"]["partial_source"]["backend_calls_saved"]
    metrics = {"full_declared_binding_state_results_matched_original_bfs": full_state_results,
        "original_declared_binding_state_results": declared_state_results,
        "unique_grounded_bindings": len(old), "unique_binding_state_results": len(old)*len(d.states),
        "partial_guards_independently_verified": guards_by_mode,
        "all_guard_completions_nonempty_sound_and_deletion_minimal": True,
        "all_complete_nogood_unions_equal_full_masks": True, "compiled_complete_match_tables_identical_to_full": True,
        "trace_decisions_independently_replayed": index, "ready_suffixes_independently_replayed": ready,
        "full_extra_vs_source_partial": len(extra), "full_extra_vs_source_partial_by_stage": Counter(r["stage"] for r in extra),
        "full_extra_vs_complete_partial": 0,
        "untimed_binding_control": {"full_state_symmetry_backend_calls": symmetry_backend,
            "full_state_all_target_bindings_backend_calls": typed_backend,
            "source_partial_backend_calls": summary["methods"]["partial_source"]["backend_calls"],
            "extra_from_deleting_state_facts_at_same_binding_scope": len(partial_state_gain),
            "status": "post_hoc_coverage_only_not_timing_arm"},
        "model_calls": 0, "physical_execution": False}
    write(out / "metrics.json", metrics)
    write(out / "guard_deletion_witnesses.json", guard_checks)
    write(out / "full_additional_coverage.json", extra)
    write(out / "partial_state_gain_same_bindings.json", partial_state_gain)
    close(out, {str(Path(__file__)): digest(__file__),
        "scripts/analyze_fragment_execution.py": digest("scripts/analyze_fragment_execution.py")},
        {str(f / "manifest.json"): digest(f / "manifest.json") for f in (COMPARISON, ORIGINAL, PREVIOUS)})
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
