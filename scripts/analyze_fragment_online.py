"""Independent accounting and explanatory guard ablations after frozen runs."""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from ch3.context_memory.cases import load_cases
from ch3.revision.fragments import Atom, contract
from ch3.revision.fragment_regions import RegionBank, STATE_INDEX, features, task_mask
from ch3.revision.history import Snapshot, State, decide, monitor, successors
from scripts.benchmark_fragment_online import check_source
from scripts.benchmark_revision_recovery_pilot import write


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def verify_suffix(snapshot, plan):
    state = snapshot.state
    for e in plan:
        state = next(ns for ns, candidate in successors(state, e.seq) if candidate == e)
    assert monitor(snapshot.contract, state, plan) == (frozenset(), frozenset())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path("data/collections")
    source = root / "fragment_online_source_20261116_v1"
    target = root / "fragment_online_transfer_20261116_v1"
    audit_source = root / "fragment_strong_control_source_20261116_v2"
    audit_target = root / "fragment_strong_control_transfer_20261116_v1"
    check_source(source)
    m = read(target / "manifest.json")
    for file, expected in m["artifact_hashes"].items():
        assert digest(target / file) == expected
    assert all(digest(f) == h for f, h in m["source_hashes"].items())
    ss, ts = read(source / "summary.json"), read(target / "summary.json")
    old_source_rows = read(root / "revision_fragments_20261116_v1/results.json")
    reference_source = {r["id"]: r["reference_status"] for r in old_source_rows}
    for name in ("fixed", "learned"):
        assert all(r["status"] == reference_source[r["id"]]
                   for r in read(source / f"{name}_source_trace.json"))
    patterns = read(source / "learned_patterns.json")
    assert patterns == read(audit_source / "learned_patterns.json")
    witnesses = read(audit_source / "deletion_witnesses.json")
    minimality = []
    for witness in witnesses:
        atoms = tuple(Atom(**a) for a in witness["core"])
        state = State(**witness["source_state"])
        snap = Snapshot(contract(atoms), state, (), "independent-core-replay")
        assert decide(snap, snap.contract, 8).status == "conflict"
        deletion = []
        for atom in atoms:
            reduced = Snapshot(contract(set(atoms) - {atom}), state, (), "deletion-replay")
            result = decide(reduced, reduced.contract, 8)
            assert result.status == "ready"
            verify_suffix(reduced, result.plan)
            deletion.append({"removed": asdict(atom), "plan_length": len(result.plan)})
        feasible = None
        for horizon in range(9, 13):
            result = decide(snap, snap.contract, horizon)
            if result.status == "ready":
                verify_suffix(snap, result.plan)
                feasible = {"horizon": horizon, "plan": [asdict(e) for e in result.plan]}
                break
        minimality.append({"source_id": witness["source_id"], "core": witness["core"],
                           "state": witness["source_state"], "deletion": deletion,
                           "first_feasible_higher_budget": feasible})
    fixed = RegionBank(read(source / "fixed_certificates.json"))
    certs = read(source / "learned_certificates.json")
    new_certs = [c for c in certs if c["pattern"] in patterns]
    unguarded = {task_mask(tuple(Atom(**a) for a in b["atoms"]))
                 for c in new_certs for b in c["bindings"]}
    grounded = [(STATE_INDEX[w["source_state"]["blue"], w["source_state"]["yellow"]],
                 task_mask(tuple(Atom(**a) for a in w["core"]))) for w in witnesses]
    memo = {tuple(r["key"]) for r in read(source / "shared_memo.json")}
    included = set(read(root / "fragment_regions_disjoint_20261116_v1/included_ids.json"))
    reference = {r["id"]: r["status"] for r in read(root / "fragment_regions_validation_20261116_v1/decisions.json")}
    ablation = {"target_n": 0, "unguarded_matches": 0, "unguarded_false_conflicts": 0,
                "grounded_core_extra_beyond_fixed_and_cache": 0,
                "certified_lifted_extra_beyond_fixed_and_cache":
                    read(audit_target / "summary.json")["additional_backend_calls_avoided"],
                "post_hoc_explanatory_analysis": True}
    for case in load_cases(root / "fragment_regions_validation_20261116_v1/cases.json"):
        if case["id"] not in included:
            continue
        snap = case["snapshot"]
        mask, state_index = features(snap)
        unguarded_hit = any(mask & bits == bits for bits in unguarded)
        ablation["target_n"] += 1
        ablation["unguarded_matches"] += unguarded_hit
        ablation["unguarded_false_conflicts"] += unguarded_hit and reference[case["id"]] != "conflict"
        base_hit = fixed.match(snap) or (mask, state_index) in memo
        grounded_hit = any(index == state_index and mask & bits == bits for index, bits in grounded)
        ablation["grounded_core_extra_beyond_fixed_and_cache"] += grounded_hit and not base_hit
    total_calls = {}
    source_times = read(source / "timings.json")
    assert ablation["target_n"] == ts["n"]
    for name in ("fixed", "learned"):
        certificates = read(source / f"{name}_certificates.json")
        reload_calls = sum(len(b["states"]) for c in certificates for b in c["bindings"])
        row = ss["methods"][name]
        total_calls[name] = {"source_full_task_backend": row["backend_calls"],
                             "initial_shared_rule_certification": next(
                                 r["fixed_certification_calls"] for r in source_times if r["method"] == name),
                             "source_learning_core_oracle": row["learning_oracle_calls"],
                             "target_full_task_backend": ts["methods"][name]["backend_calls"],
                             "reload_recertification_oracle": reload_calls}
        total_calls[name]["total"] = sum(total_calls[name].values())
    paired = []
    source_times, target_times = read(source / "timings.json"), read(target / "timings.json")
    for repeat in range(3):
        row = {"repeat": repeat}
        for name in ("fixed", "learned"):
            st = next(r for r in source_times if r["repeat"] == repeat and r["method"] == name)
            tt = next(r for r in target_times if r["repeat"] == repeat and r["method"] == name)
            extra = ts["methods"][name]["full_preparation_ms"] - ss["methods"][name]["median_preparation_ms"]
            row[name + "_total_ms"] = st["preparation_ms"] + ss["memo_merge_ms"] + extra + tt["wall_ms"]
        row["saved_ms"] = row["fixed_total_ms"] - row["learned_total_ms"]
        paired.append(row)
    a, b = ts["methods"]["fixed"], ts["methods"]["learned"]
    metrics = {"backend_reduction_percent": (a["backend_calls"] - b["backend_calls"]) / a["backend_calls"] * 100,
               "query_reduction_percent": (a["median_query_ms"] - b["median_query_ms"]) / a["median_query_ms"] * 100,
               "total_reduction_percent": (a["median_total_ms"] - b["median_total_ms"]) / a["median_total_ms"] * 100,
               "paired_totals": paired, "total_oracle_accounting": total_calls,
               "verified_ready_plans_per_method_per_round": a["verified_ready_plans"],
               "retrospective": True, "model_calls": 0, "physical_execution": False}
    write(args.output / "metrics.json", metrics)
    write(args.output / "guard_ablation.json", ablation)
    write(args.output / "core_replay.json", minimality)
    write(args.output / "verification.json", {"source_statuses_independently_match_archive": True,
          "new_patterns_match_independent_source_audit": True,
          "core_infeasibility_and_deletion_plans_replayed": True,
          "all_source_and_target_hashes_verified": True})
    write(args.output / "manifest.json", {"analysis_script_sha256": digest(__file__),
          "input_manifests": {str(x): digest(x) for x in
              (source / "manifest.json", target / "manifest.json", audit_source / "manifest.json", audit_target / "manifest.json")},
          "artifact_hashes": {p.name: digest(p) for p in args.output.glob("*.json")}})
    print(json.dumps({"metrics": metrics, "ablation": ablation, "core_replay": minimality}), flush=True)


if __name__ == "__main__":
    main()
