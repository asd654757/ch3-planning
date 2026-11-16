"""Audit failure-derived region learning, source separation and paired costs."""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from ch3.context_memory.cases import load_cases
from ch3.revision.fragments import Atom, contract
from ch3.revision.fragment_regions import RegionBank
from ch3.revision.history import Snapshot, State, decide
from ch3.revision.evaluation import valid_suffix
from scripts.benchmark_fragment_regions import train
from scripts.benchmark_revision_fragments import signature, temporal_probes
from scripts.benchmark_revision_recovery_pilot import write


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def check_archive(path):
    manifest = json.loads((path/"manifest.json").read_text())
    for source, expected in manifest["source_hashes"].items():
        assert digest(source) == expected, source
        archived = path/"sources"/source
        if archived.exists():
            assert digest(archived) == expected, source
    for name, expected in manifest.get("frozen_artifact_hashes", {}).items():
        assert digest(path/name) == expected, name
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--training", type=Path, required=True)
    p.add_argument("--validation", type=Path, required=True)
    p.add_argument("--disjoint", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    tm, vm, dm = map(check_archive, (args.training, args.validation, args.disjoint))
    source = Path("data/collections/context_memory_validation_20261116_v2")
    old_manifest = json.loads(Path("data/collections/revision_fragments_20261116_v1/manifest.json").read_text())
    assert digest(source/"results.json") == old_manifest["source_results_sha256"]
    assert digest(source/"cases.json") == old_manifest["source_cases_sha256"]
    calibration = load_cases(args.training/"calibration_cases.json")
    replay = args.output/"training_replay"
    replay.mkdir()
    _, replay_training = train(calibration, replay)
    for name in ("calibration_trace", "seed_patterns", "learned_patterns", "expert_certificates", "seed_certificates", "learned_certificates"):
        assert json.loads((replay/f"{name}.json").read_text()) == json.loads((args.training/f"{name}.json").read_text()), name
    training = json.loads((args.training/"training.json").read_text())
    for key in ("instrumented_core_oracle_calls", "calibration_backend_calls", "initial_patterns", "new_calibration_patterns", "verified_model_rejections"):
        assert training[key] == replay_training[key]
    expert = RegionBank(json.loads((args.training/"expert_certificates.json").read_text()))
    learned = RegionBank(json.loads((args.training/"learned_certificates.json").read_text()))
    cases = load_cases(args.validation/"cases.json")
    reference = {r["id"]: r["status"] for r in json.loads((args.validation/"decisions.json").read_text())}
    assert len(cases) == len(reference) == 56784
    assert digest(args.validation/"cases.json") == dm["source_cases_sha256"]
    assert digest(args.validation/"decisions.json") == dm["source_reference_sha256"]
    seen = set()
    for path, expected in dm["seen_input_hashes"].items():
        assert digest(path) == expected
        seen.update(signature(c["snapshot"]) for c in load_cases(Path(path)))
    excluded, included = [], []
    gained, lost, false_conflicts = [], [], []
    direct_gained_replays = 0
    hits = {"expert": 0, "learned": 0}
    for case in cases:
        s = case["snapshot"]
        is_seen = signature(s) in seen
        (excluded if is_seen else included).append(case["id"])
        e, l = expert.match(s), learned.match(s)
        if not is_seen:
            hits["expert"] += e
            hits["learned"] += l
        if (e or l) and reference[case["id"]] != "conflict":
            false_conflicts.append(case["id"])
        if l and not e and not is_seen:
            assert decide(s, s.contract, 8).status == "conflict"
            direct_gained_replays += 1
            gained.append({"id": case["id"], "evidence": learned.evidence(s)})
        if e and not l:
            lost.append(case["id"])
    assert excluded == json.loads((args.disjoint/"excluded_ids.json").read_text())
    assert included == json.loads((args.disjoint/"included_ids.json").read_text())
    assert digest(args.disjoint/"included_ids.json") == dm["included_ids_sha256"]
    assert digest(args.disjoint/"excluded_ids.json") == dm["excluded_ids_sha256"]
    assert not false_conflicts and not lost
    regression = []
    for path in (Path("data/collections/context_memory_development_20261113_v1/cases.json"), source/"cases.json"):
        regression.extend(load_cases(path))
    regression.extend(temporal_probes())
    for case in regression:
        s = case["snapshot"]
        if learned.match(s):
            assert decide(s, s.contract, 8).status == "conflict"
    # A concrete nine-action witness explains the learned eight-action conflict.
    witness_snapshot = Snapshot(contract([Atom("at", "blue", "green"), Atom("visit", "yellow", "green")]),
                                State("green", "hand", "yellow"), (), "budget-witness").validate()
    d8 = decide(witness_snapshot, witness_snapshot.contract, 8)
    d9 = decide(witness_snapshot, witness_snapshot.contract, 9)
    assert d8.status == "conflict" and d9.status == "ready" and len(d9.plan) == 9
    assert valid_suffix(witness_snapshot, witness_snapshot.contract, d9.plan)
    assert learned.match(witness_snapshot, 8) and not learned.match(witness_snapshot, 9)
    timings = json.loads((args.disjoint/"timings.json").read_text())
    assert len(timings) == 10 and len({r["status_signature"] for r in timings}) == 1
    summary = json.loads((args.disjoint/"summary.json").read_text())
    pairs = []
    for repeat in range(5):
        row = {r["method"]: r for r in timings if r["repeat"] == repeat}
        for method in ("expert", "learned"):
            assert row[method]["hits"] == hits[method]
            assert row[method]["backend_calls"] == len(included)-hits[method]
            assert row[method]["correct"] == len(included)
        costs = {m: row[m]["wall_ms"]+summary["methods"][m]["build_and_load_ms"] for m in row}
        pairs.append({"repeat": repeat,
            "wall_saved_ms": row["expert"]["wall_ms"]-row["learned"]["wall_ms"],
            "cpu_saved_ms": row["expert"]["cpu_ms"]-row["learned"]["cpu_ms"],
            "including_preparation_saved_ms": costs["expert"]-costs["learned"]})
    a, b = summary["methods"]["expert"], summary["methods"]["learned"]
    metrics = {"warm_median_reduction_pct": 100*(1-b["median_wall_ms"]/a["median_wall_ms"]),
        "including_preparation_median_reduction_pct": 100*(1-b["median_including_preparation_ms"]/a["median_including_preparation_ms"]),
        "backend_call_reduction_pct": 100*(1-b["backend_calls"]/a["backend_calls"]),
        "warm_faster_repeats": sum(r["wall_saved_ms"] > 0 for r in pairs),
        "including_preparation_faster_repeats": sum(r["including_preparation_saved_ms"] > 0 for r in pairs)}
    result = {"n_complete_grid": len(cases), "n_source_disjoint": len(included),
        "excluded_seen_signatures": excluded, "automated_training_and_certificates_reproduced": True,
        "all_false_conflicts": false_conflicts, "lost_expert_coverage": lost,
        "new_covered_queries_rechecked_by_original_solver": direct_gained_replays,
        "history_boundary_regressions": len(regression), "paired_timing": pairs, "metrics": metrics,
        "model_calls": 0, "physical_execution": False,
        "scope": "finite-domain bounded recovery-decision computation; not robot success or LLM savings"}
    write(args.output/"analysis.json", result)
    write(args.output/"new_coverage_evidence.json", gained)
    write(args.output/"nine_action_witness.json", {"snapshot": witness_snapshot,
        "status_h8": d8.status, "status_h9": d9.status, "plan_h9": d9.plan,
        "h9_plan_replayed": True, "h8_certificate_abstains_at_h9": True})
    write(args.output/"manifest.json", {"analysis_source_sha256": digest(__file__), "model_calls": 0,
        "training": str(args.training), "validation": str(args.validation), "disjoint": str(args.disjoint)})
    print(json.dumps(result))


if __name__ == "__main__":
    main()
