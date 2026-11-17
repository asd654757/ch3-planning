"""Fresh-process audit of serialized architecture traces and paired outcomes."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_purpose_repair import PurposePolicy, admissible
from scripts.analyze_fragment_execution import decode
from scripts.benchmark_fragment_purpose_repair import reference_rows
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write


def event_key(event):
    return event.seq, event.kind, event.object, event.target


def main():
    source = ROOT/"architecture_loop_20261117_v1"
    output = ROOT/"architecture_loop_audit_20261117_v1"
    check(source)
    output.mkdir(parents=True, exist_ok=False)
    d = build_compact_domain()
    summary = read(source/"summary.json")
    decisions, optima, outcomes, hashes, metrics = {}, {}, {}, {}, {}
    for arm, record in summary["methods"].items():
        rows = read(source/f"{arm}_trace.json")
        counts, previous, normalized, completed = Counter(), {}, [], {}
        for row in rows:
            steps = [(step, decode(d, step["snapshot"])) for step in row["decisions"]]
            first = steps[0][1]
            if row["episode"] in previous:
                prior = previous[row["episode"]]
                assert first.state == prior.state and first.history == prior.history
                assert first.contract.version == prior.contract.version+1
            else:
                assert first.state == d.model.State() and not first.history
            for step, s in steps:
                if s.digest not in decisions:
                    decisions[s.digest] = d.model.decide(s, s.contract, 8)
                gold, decision = decisions[s.digest], step["decision"]
                assert decision["status"] == gold.status
                plan = tuple(d.model.Event(**e) for e in decision["plan"])
                if decision["status"] == "ready":
                    state = s.state
                    assert len(plan) <= 8
                    for offset, event in enumerate(plan, 1):
                        assert event.seq == s.cursor+offset
                        state = next(ns for ns, e in d.model.successors(state, event.seq)
                                     if event_key(e) == event_key(event))
                    assert all(d.model.monitor(s.contract, state, (*h, *plan)) == (frozenset(), frozenset())
                               for h in d.model.possible_histories(s.history))
                    counts["ready_suffixes_checked"] += 1
                else:
                    assert not plan
                    for name in ("history_alternatives", "feasible_alternatives", "disputed_events", "violated_clauses"):
                        expected = getattr(gold, name)
                        assert decision[name] == (list(expected) if isinstance(expected, tuple) else expected)
                counts["decisions_checked"] += 1
            covered = set()
            for action in row["actions"]:
                step, before = steps[action["before_step"]]
                post, after = steps[action["after_step"]]
                requested = d.model.Event(**step["decision"]["plan"][0])
                assert action["command"]["skill"] == requested.kind
                assert action["command"]["object_id"] == requested.object+"_candidate"
                assert action["command"]["arm"] == "right" and action["command"]["step_id"] == 1
                target = {"table": "table", "green": "green_region", "return": "return_region"}.get(requested.target)
                assert action["command"]["target_id"] == target
                events = tuple(d.model.Event(**e) for e in action["events"])
                assert before.contract == after.contract and after.history == (*before.history, *events)
                assert action["after_step"] == action["before_step"]+1
                assert post["generation"] == step["generation"]+1
                if events:
                    event, = events
                    assert event.evidence == "confirmed" and event.observation_id == after.observation_id
                    assert any(state == after.state and event_key(e) == event_key(event)
                               for state, e in d.model.successors(before.state, before.cursor+1))
                else:
                    assert before.state == after.state
                covered.add(action["after_step"])
                counts["command_target_and_transition_checks"] += 1
            for repair in row["repairs"]:
                step, before = steps[repair["before_step"]]
                issued = repair["issued"]
                p, raw_policy = issued["proposal"], issued["policy"]
                policy = PurposePolicy(raw_policy["id"], raw_policy["snapshot_digest"],
                    tuple(raw_policy["swappable_objects"]), tuple(raw_policy["equivalent_targets"]))
                assert p["snapshot_digest"] == before.digest == policy.snapshot_digest
                assert issued["generation"] == step["generation"]
                if before.digest not in optima:
                    result = ("no_permitted_substitution", None, ())
                    for cost, edits, task in reference_rows(d, before, policy):
                        if d.model.decide(before, task, 8).status == "ready":
                            result = ("proposal", cost, edits)
                            break
                    optima[before.digest] = result
                assert (p["status"], p["cost"], tuple((e["id"], e["target"]) for e in p["edits"])) == optima[before.digest]
                counts["repair_searches_checked"] += 1
                if repair["accepted"]:
                    post, after = steps[repair["after_step"]]
                    assert admissible(d, before, policy, after.contract)
                    assert before.state == after.state and before.history == after.history
                    assert repair["after_step"] == repair["before_step"]+1
                    assert post["generation"] == step["generation"]+1
                    covered.add(repair["after_step"])
                    counts["adoptions_checked"] += 1
            assert covered == set(range(1, len(steps)))
            final = decode(d, row["final_snapshot"])
            assert final == steps[-1][1]
            if row["completed"]:
                assert all(d.model.monitor(final.contract, final.state, h) == (frozenset(), frozenset())
                           for h in d.model.possible_histories(final.history))
            previous[row["episode"]] = final
            completed[row["episode"], row["revision"]] = row["completed"]
            normalized.append({"episode": row["episode"], "revision": row["revision"],
                "initial_status": row["initial_status"], "completed": row["completed"],
                "final_status": row["final_status"], "final_snapshot": final.digest,
                "decisions": [(step["kind"], s.digest, step["decision"]["status"],
                    [event_key(d.model.Event(**e)) for e in step["decision"]["plan"]]) for step, s in steps],
                "actions": [(a["command"], a["observation"], [event_key(d.model.Event(**e)) for e in a["events"]])
                            for a in row["actions"]],
                "repairs": [(r["accepted"], r["issued"]["proposal"]["status"], r["issued"]["proposal"]["cost"],
                    [(e["id"], e["target"]) for e in r["issued"]["proposal"]["edits"]]) for r in row["repairs"]]})
        assert sum(completed.values()) == record["metrics"]["completed"]
        runtime = read(source/f"{arm}_runtime_decisions.json")
        assert sum(r["origin"] == "backend" for r in runtime) == record["metrics"]["runtime_backend_calls"]
        assert sum(r["issued"]["proposal"]["stats"]["backend_calls"] for row in rows for r in row["repairs"]) == record["metrics"]["repair_backend_calls"]
        for name in ("decisions_checked", "ready_suffixes_checked", "repair_searches_checked", "adoptions_checked"):
            assert counts[name] == record["metrics"].get(name, 0)
        outcomes[arm], metrics[arm] = completed, dict(counts)
        hashes[arm] = sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        print(json.dumps({"arm": arm, "completed": sum(completed.values()), "counts": dict(counts)}), flush=True)
    assert len({hashes[name] for name in ("standard", "fixed", "source_partial", "learned")}) == 1
    matrix = Counter()
    for key, with_repair in outcomes["learned"].items():
        without_repair = outcomes["no_repair"][key]
        matrix[f"repair_{int(with_repair)}_no_repair_{int(without_repair)}"] += 1
    report = {"all_serialized_statuses_suffixes_targets_ledgers_and_adoptions_checked": True,
        "main_four_semantic_traces_identical": True, "unique_reference_snapshots": len(decisions),
        "unique_repair_optima_checked": len(optima), "per_arm_checks": metrics,
        "semantic_hashes": hashes, "paired_completion_matrix": dict(matrix),
        "physical_execution": False, "model_calls": 0}
    write(output/"metrics.json", report)
    paths = [Path(__file__).resolve().relative_to(Path.cwd()), Path("scripts/analyze_fragment_execution.py"),
             Path("scripts/benchmark_fragment_purpose_repair.py"), Path("ch3/revision/fragment_purpose_repair.py")]
    close(output, {str(p): digest(p) for p in paths}, {str(source/"manifest.json"): digest(source/"manifest.json")})
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
