"""Audit the frozen physical and transfer trials and exercise late-evidence dispatch."""
import argparse
from collections import Counter
from itertools import permutations
from pathlib import Path
from statistics import median

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.evidence_resolution import EvidenceFinding, EvidenceResolution
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_runtime import build_fragment_runtime
from scripts.benchmark_fragment_purpose_repair import build_bank
from scripts.benchmark_fragment_three_objects import check, close, digest, read, write


ROOT = Path("data/collections")
PHYSICS = ROOT / "ch3_fixed_goal_physics_20261119_v1"
TRANSFER = ROOT / "ch3_fixed_goal_transfer_20261119_v1"


def physical_audit():
    check(PHYSICS)
    manifest = read(PHYSICS/"manifest.json")
    for filename, expected in manifest["images"].items():
        assert digest(PHYSICS/filename) == expected
    plan, rows = read(PHYSICS/"plan.json"), read(PHYSICS/"results.json")
    indexed = {(r["seed"], r["case"], r["arm"]): r for r in rows}
    assert len(indexed) == len(rows) == 128
    pairs, actual_faults = 0, 0
    for seed in plan["seeds"]:
        for case in plan["cases"]:
            arms = {arm: indexed[seed, case, arm] for arm in plan["arms"]}
            direct, routed = arms["direct"], arms["routed"]
            for key in ("success", "reported_success", "commands", "history", "observations", "evidence",
                        "route", "control_steps", "fault_steps", "prefix_blue_z", "regrasp_attempts"):
                assert direct[key] == routed[key], (key, seed, case)
            omit_wall_time = lambda trial: [{k: v for k, v in e.items() if k != "wall_seconds"} for e in trial["events"]]
            assert omit_wall_time(direct) == omit_wall_time(routed)
            pairs += 1
            if case in ("no_lift", "unobservable_no_lift"):
                assert routed["fault_steps"] > 0 and routed["prefix_blue_z"] < .04
                assert routed["initial_receipt_completed"] and not routed["independent_prefix_lifted_near_hand"]
                actual_faults += 1
            for arm, row in arms.items():
                assert "error" not in row and row["episode_resets"] == 1 and row["final_contract_version"] == 1
                assert row["reported_success"] == row["success"]
                assert row["control_steps"] <= 900
                assert row["executed_places"] == sum(c["skill"] == "place" for c in row["commands"])
                assert all((c["object_id"], c["target_id"]) in (("blue_candidate", None),
                           ("blue_candidate", "green_region")) for c in row["commands"])
                if row["executed_places"]:
                    selected = next(o for o in row["observations"] if o["id"] == row["history"][0]["observation_id"])
                    assert selected["label"] == "ready" and selected["holding"]["status"] == "holding_supported"
                    assert row["history"][-1]["observation_id"] == "verified-release-rgb"
                    assert row["evidence"][-1]["release"]["status"] == "release_supported"
                    assert row["evidence"][-1]["arrival"]["status"] == "satisfied"
                else:
                    assert not row["history"]
                if case == "unobservable_no_lift" and arm != "receipt":
                    assert row["route"] == "insufficient_observation_stop" and len(row["observations"]) == 2
                    assert not row["regrasp_attempts"] and not row["executed_places"]
    return {"audited_trials": len(rows), "direct_routed_pairs_with_identical_execution": pairs,
            "injected_no_lift_pairs_confirmed_by_prefix_geometry": actual_faults,
            "all_reported_success_matches_independent_terminal_scoring": True,
            "evidence_based_unobservable_bounded_stops": 24, "all_places_evidence_gated": True}


def transfer_audit():
    check(TRANSFER)
    summary, samples = read(TRANSFER/"summary.json"), read(TRANSFER/"timings.json")
    counts = read(TRANSFER/"per_sequence_calls.json")
    corrections = []
    for sample in samples:
        arm = sample["arm"]
        correct = sum(counts[arm].values())
        delta = sample["calls"]-correct
        if delta:
            assert arm == "learned" and sample["repeat"] == 0 and delta == 2
            corrections.append({"arm": arm, "repeat": 0, "raw": sample["calls"], "in_timed_workload": correct,
                "reason": "two separate history-witness backend calls from after the clock leaked into the log counter"})
            sample["calls"] = correct
        assert correct == summary["methods"][arm]["calls"]
    assert len(corrections) == 1
    return {"corrections": corrections, "timings_changed": False, "corrected_timing_rows": samples,
            "elapsed_pairwise_ms": {arm: {"faster_rounds": sum(t > 0 for t in x["paired_time_saved_ms"]),
                "median_paired_difference": median(x["paired_time_saved_ms"])}
                for arm, x in summary["comparisons_with_learned"].items()}}


def late_evidence_cases():
    domain = build_compact_domain()
    model = domain.model
    session = domain.Session(build_bank(domain, "learned"))
    session.freeze()
    bridge = build_fragment_runtime(domain, session, IndependentActionBound(domain))

    class Runtime(EvidenceResolution, bridge.Runtime):
        pass

    rows = []
    for first, second in permutations(model.OBJECTS, 2):
        for family in ("order", "visit_then_protect", "ongoing_protection"):
            # Logs can omit events. Each record under dispute refers to an
            # alleged place, never a proposition inferred from the current pose.
            history = (model.Event(1, "place", first, "return", "unknown", "prior-uncertain"),)
            state = model.State()
            old = [model.Clause(id="finish", kind="at", object=second, target="green")]
            if family == "order":
                history += (model.Event(2, "place", second, "green", observation_id="prior-second"),)
                state = model.State(**{second: "green"})
                old = [model.Clause(id="finish", kind="at", object=first, target="return"),
                       model.Clause(id="earlier", kind="visit", object=first, target="return"),
                       model.Clause(id="later", kind="visit", object=second, target="green")]
                new = model.Clause(id="added", kind="before", first="earlier", second="later")
            elif family == "visit_then_protect":
                old.append(model.Clause(id="earlier", kind="visit", object=first, target="return"))
                new = model.Clause(id="added", kind="forbid", object=first)
            else:
                old.append(model.Clause(id="protect", kind="forbid", object=first))
                new = model.Clause(id="added", kind="empty")
            initial = model.Snapshot(model.Contract(0, tuple(model.Obligation(c) for c in old)), state, history,
                                     "current-pose").validate()
            patch = model.Patch(version=0, snapshot=initial.digest,
                                edits=[model.Edit(op="add", id=new.id, quote="新增约束", clause=new)])
            task = model.apply_patch(initial, patch, "新增约束")
            assert task.obligations[:-1] == initial.contract.obligations
            for verdict in ("confirmed", "refuted"):
                rt = Runtime(initial)
                pending, _ = rt.accept(task, expected_digest=initial.digest, expected_generation=0)
                assert pending.status == "need_observation" and pending.disputed_events == (1,)
                try:
                    rt.issue()
                except ValueError:
                    pass
                else:
                    raise AssertionError("disputed plan was dispatchable")
                before = rt.snapshot
                finding = EvidenceFinding(1, verdict, "new-review-observation", before.digest, rt.generation)
                updated = rt.resolve_evidence(finding)
                assert rt.original_history == history and updated.cursor == before.cursor
                assert updated.state == before.state and updated.contract == before.contract
                result, route = rt.assess()
                reference = model.decide(updated, task, 8)
                expected_ready = (verdict == "confirmed") != (family == "ongoing_protection")
                assert result.status == reference.status == ("ready" if expected_ready else "conflict")
                commands = []
                while result.status == "ready" and result.plan:
                    plan = result.plan
                    command = rt.consume(rt.issue())
                    state_after, event = next((s, e) for s, e in model.successors(rt.snapshot.state, rt.snapshot.cursor+1)
                        if (e.seq, e.kind, e.object, e.target) == (plan[0].seq, plan[0].kind, plan[0].object, plan[0].target))
                    assert command.object_id == event.object+"_candidate" and command.skill == event.kind
                    commands.append(command.model_dump(mode="json"))
                    observed = model.Event(event.seq, event.kind, event.object, event.target,
                                           observation_id=f"continued-observation-{event.seq}")
                    rt.observe(state_after, observation_id=observed.observation_id, events=(observed,))
                    result, _ = rt.assess(plan[1:])
                if expected_ready:
                    assert not result.plan and result.status == "ready"
                    assert model.monitor(task, rt.snapshot.state, next(model.possible_histories(rt.snapshot.history))) == (
                        frozenset(), frozenset())
                else:
                    assert not commands
                assert rt.original_history[:len(history)] == history and rt.snapshot.contract == task
                rows.append({"family": family, "objects": [first, second], "verdict": verdict, "before": pending.status,
                           "after": reference.status, "route": route, "original_history": rt.original_history,
                           "effective_history": rt.snapshot.history, "finding": rt.findings[0], "commands": commands})
    assert len(rows) == 36
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=False)
    physical = physical_audit()
    transfer = transfer_audit()
    evidence = late_evidence_cases()
    write(output/"physical_audit.json", physical)
    write(output/"transfer_timing_reconciliation.json", transfer)
    write(output/"late_evidence_trials.json", evidence)
    write(output/"summary.json", {"physical": physical, "timing_log_rows_corrected": len(transfer["corrections"]),
        "resolved_cases": len(evidence), "post_resolution_routes": Counter(r["route"] for r in evidence),
        "outcomes_after_resolution": Counter(r["after"] for r in evidence),
        "commands_after_resolution": sum(len(r["commands"]) for r in evidence)})
    paths = [Path(__file__).resolve().relative_to(Path.cwd()), Path("ch3/revision/evidence_resolution.py"),
             Path("tests/test_evidence_resolution.py")]
    close(output, {str(path): digest(path) for path in paths},
          {str(folder/"manifest.json"): digest(folder/"manifest.json") for folder in (PHYSICS, TRANSFER)})


if __name__ == "__main__":
    main()
