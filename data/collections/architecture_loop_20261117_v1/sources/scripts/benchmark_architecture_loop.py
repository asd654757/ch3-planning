"""Existing symbolic execution loop plus adopted, permission-bound task repair."""
import argparse
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_purpose_repair import SharedReadyCache, admissible
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.repair_coordinator import RepairCoordinator
from scripts.benchmark_fragment_execution import action_key
from scripts.benchmark_fragment_purpose_repair import build_bank, fixture_policy, reference_rows, SOURCE, PARTIAL
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write

PROTOCOL = Path("docs/architecture_loop_protocol_20261117.md")
FIXTURES = ROOT / "fragment_execution_fixtures_20261116_v1"
ARMS = ("standard", "fixed", "source_partial", "learned", "no_repair")


def run_loop(d, bridge, session, lower, cache, templates, arm):
    traces, m = [], d.model
    for episode in templates:
        rt = bridge.Runtime(m.Snapshot(m.Contract(0, ()), m.State(), (), episode["id"]))
        co = RepairCoordinator(d, rt, session, lower, cache, method="standard" if arm == "standard" else "recheck")
        for revision in episode["revisions"]:
            obligations = tuple(m.Obligation(m.Clause(id=f"r{revision['index']}-{i}",
                **{k: v for k, v in atom.items() if v}), rt.snapshot.cursor) for i, atom in enumerate(revision["atoms"]))
            task = m.Contract(rt.snapshot.contract.version+1, obligations)
            result, _ = rt.accept(task, expected_digest=rt.snapshot.digest, expected_generation=rt.generation)
            row = {"episode": episode["id"], "revision": revision["index"], "initial_status": result.status,
                "decisions": [], "actions": [], "repairs": [], "action_budget_exhausted": False}

            def record(kind):
                row["decisions"].append({"kind": kind, "snapshot": rt.snapshot,
                                          "decision": result, "generation": rt.generation})

            record("task_request")
            attempted = False
            while True:
                if result.status == "conflict" and not attempted and arm != "no_repair":
                    attempted = True
                    issued = co.suggest(fixture_policy(d, rt.snapshot))
                    repair = {"before_step": len(row["decisions"])-1, "issued": issued, "accepted": False}
                    row["repairs"].append(repair)
                    if issued.proposal.status != "proposal":
                        break
                    result, _ = co.apply(issued)
                    record("repair_adoption")
                    repair.update(accepted=True, after_step=len(row["decisions"])-1)
                if result.status != "ready" or not result.plan:
                    break
                if len(row["actions"]) >= 20:
                    row["action_budget_exhausted"] = True
                    break
                requested, before = result.plan[0], rt.snapshot
                before_step = len(row["decisions"])-1
                command = rt.consume(rt.issue())
                assert rt.snapshot is before
                observation = revision["first_action_observation"] if not row["actions"] else "normal"
                choices = list(m.successors(before.state, before.cursor+1))
                if observation == "foreign_legal_event":
                    alternatives = [(s, e) for s, e in choices if action_key(e) != action_key(requested)]
                    if alternatives:
                        state, event = alternatives[0]
                    else:
                        observation = "foreign_unavailable_no_effect"
                if observation in ("no_effect", "foreign_unavailable_no_effect"):
                    state, events, remaining = before.state, (), result.plan
                else:
                    if observation == "normal":
                        state, event = next((s, e) for s, e in choices if action_key(e) == action_key(requested))
                    obs_id = f"{episode['id']}-r{revision['index']}-a{len(row['actions'])}"
                    events = (replace(event, observation_id=obs_id),)
                    remaining = result.plan[1:] if observation == "normal" else result.plan
                obs_id = f"{episode['id']}-r{revision['index']}-a{len(row['actions'])}"
                rt.observe(state, observation_id=obs_id, events=events)
                result, _ = rt.assess(remaining)
                record("observation")
                row["actions"].append({"before_step": before_step, "after_step": len(row["decisions"])-1,
                    "command": command.model_dump(mode="json"), "observation": observation, "events": events})
            row.update(completed=result.status == "ready" and not result.plan,
                final_status=result.status, final_snapshot=rt.snapshot)
            traces.append(row)
    return traces


def semantic_digest(traces):
    value = []
    for row in traces:
        value.append({"episode": row["episode"], "revision": row["revision"],
            "initial_status": row["initial_status"], "completed": row["completed"],
            "final_status": row["final_status"], "final_snapshot": row["final_snapshot"].digest,
            "decisions": [(s["kind"], s["snapshot"].digest, s["decision"].status,
                           [action_key(e) for e in s["decision"].plan]) for s in row["decisions"]],
            "actions": [(a["command"], a["observation"], [action_key(e) for e in a["events"]]) for a in row["actions"]],
            "repairs": [(r["accepted"], r["issued"].proposal.status, r["issued"].proposal.cost,
                         [(e.id, e.target) for e in r["issued"].proposal.edits]) for r in row["repairs"]]})
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def audit(d, bridge, traces, decision_reference, repair_reference):
    counts, previous = Counter(), {}
    for row in traces:
        initial = row["decisions"][0]["snapshot"]
        prior = previous.get(row["episode"])
        if prior is None:
            assert initial.state == d.model.State() and not initial.history and initial.contract.version == 1
        else:
            assert initial.state == prior.state and initial.history == prior.history
            assert initial.contract.version == prior.contract.version+1
        assert all(o.since == initial.cursor for o in initial.contract.obligations)
        for step in row["decisions"]:
            s, decision = step["snapshot"], step["decision"]
            if s.digest not in decision_reference:
                decision_reference[s.digest] = d.model.decide(s, s.contract, 8)
            gold = decision_reference[s.digest]
            assert decision.status == gold.status
            if decision.status == "ready":
                assert len(decision.plan) <= 8 and bridge.valid_suffix(s, s.contract, decision.plan)
                counts["ready_suffixes_checked"] += 1
            else:
                assert not decision.plan
                assert (decision.history_alternatives, decision.feasible_alternatives,
                    decision.disputed_events, decision.violated_clauses) == (
                    gold.history_alternatives, gold.feasible_alternatives, gold.disputed_events, gold.violated_clauses)
            counts["decisions_checked"] += 1
        transition_indices = set()
        for action in row["actions"]:
            before = row["decisions"][action["before_step"]]
            after = row["decisions"][action["after_step"]]
            a, b = before["snapshot"], after["snapshot"]
            assert action["after_step"] == action["before_step"]+1
            assert after["kind"] == "observation" and after["generation"] == before["generation"]+1
            transition_indices.add(action["after_step"])
            assert a.contract == b.contract and b.history == (*a.history, *action["events"])
            requested = before["decision"].plan[0]
            assert action["command"]["skill"] == requested.kind
            assert action["command"]["object_id"] == requested.object+"_candidate"
            if action["events"]:
                event, = action["events"]
                assert event.evidence == "confirmed" and event.observation_id == b.observation_id
                assert any(ns == b.state and action_key(e) == action_key(event)
                           for ns, e in d.model.successors(a.state, a.cursor+1))
                assert (action_key(event) == action_key(requested)) == (action["observation"] == "normal")
            else:
                assert a.state == b.state and action["observation"] in ("no_effect", "foreign_unavailable_no_effect")
            counts["action_transitions_checked"] += 1
        for repair in row["repairs"]:
            before = row["decisions"][repair["before_step"]]
            s, issued = before["snapshot"], repair["issued"]
            p, policy = issued.proposal, issued.policy
            assert p.snapshot_digest == s.digest and issued.generation == before["generation"]
            assert p.policy_id == policy.id and policy.snapshot_digest == s.digest
            if s.digest not in repair_reference:
                answer = ("no_permitted_substitution", None, ())
                for cost, edits, task in reference_rows(d, s, policy):
                    if d.model.decide(s, task, 8).status == "ready":
                        answer = ("proposal", cost, edits)
                        break
                repair_reference[s.digest] = answer
            assert (p.status, p.cost, tuple((e.id, e.target) for e in p.edits)) == repair_reference[s.digest]
            assert repair["accepted"] == (p.status == "proposal")
            if repair["accepted"]:
                after = row["decisions"][repair["after_step"]]
                a = after["snapshot"]
                assert repair["after_step"] == repair["before_step"]+1 and after["kind"] == "repair_adoption"
                assert after["generation"] == before["generation"]+1
                assert a.contract == p.contract and admissible(d, s, policy, a.contract)
                assert a.state == s.state and a.history == s.history
                transition_indices.add(repair["after_step"])
                counts["adoptions_checked"] += 1
            counts["repair_searches_checked"] += 1
        assert transition_indices == set(range(1, len(row["decisions"])))
        final = row["final_snapshot"]
        assert final == row["decisions"][-1]["snapshot"]
        if row["completed"]:
            assert all(d.model.monitor(final.contract, final.state, h) == (frozenset(), frozenset())
                       for h in d.model.possible_histories(final.history))
        previous[row["episode"]] = final
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for folder in (SOURCE, PARTIAL, FIXTURES):
        check(folder)
    args.output.mkdir(parents=True, exist_ok=False)
    paths = [PROTOCOL, Path(__file__).resolve().relative_to(Path.cwd()),
        *sorted(Path("ch3/revision").glob("*.py")), Path("tests/test_repair_coordinator.py"),
        *[Path("scripts")/name for name in ("benchmark_fragment_execution.py", "benchmark_fragment_purpose_repair.py",
          "benchmark_fragment_repair.py", "benchmark_fragment_repair_frozen.py", "benchmark_fragment_three_objects.py")]]
    hashes = {str(p): digest(p) for p in paths}
    for path in paths:
        dest = args.output/"sources"/path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    templates = read(FIXTURES/"episodes.json")
    write(args.output/"plan.json", {"arms": ARMS, "repeats": 3, "episodes": len(templates), "requests": 384,
        "horizon": 8, "max_adoptions_per_request": 1, "max_action_attempts_per_request": 20,
        "physical_execution": False, "model_calls": 0, "protocol_sha256": digest(PROTOCOL)})
    print(json.dumps({"phase": "frozen", "episodes": len(templates), "arms": ARMS}), flush=True)
    timings, references, repair_refs, first = [], {}, {}, {}
    expected = None
    for repeat in range(3):
        order = ARMS[repeat:]+ARMS[:repeat]
        if repeat == 1:
            order = tuple(reversed(order))
        for arm in order:
            started = perf_counter()
            d = build_compact_domain()
            lower = IndependentActionBound(d)
            bank = build_bank(d, "learned" if arm == "no_repair" else arm)
            raw_memo = read(SOURCE/"shared_memo.json")
            cache = SharedReadyCache(d, raw_memo)
            session = d.Session(bank)
            session.memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"])) for r in raw_memo}
            session.freeze()
            bridge = build_fragment_runtime(d, session, lower)
            prepared = perf_counter()
            traces = run_loop(d, bridge, session, lower, cache, templates, arm)
            finished = perf_counter()
            checks = audit(d, bridge, traces, references, repair_refs)
            semantic = semantic_digest(traces)
            if arm != "no_repair":
                if expected is None:
                    expected = semantic
                assert semantic == expected
            assert bridge.unchanged_runtime_methods and len(session.memo) == len(raw_memo) and not session.patterns
            searches = [r for row in traces for r in row["repairs"]]
            adopted = [r for r in searches if r["accepted"]]
            repair_calls = sum(r["issued"].proposal.stats["backend_calls"] for r in searches)
            metrics = {"completed": sum(r["completed"] for r in traces), "repair_searches": len(searches),
                "adoptions": len(adopted), "completed_after_adoption": sum(r["completed"] and any(x["accepted"] for x in r["repairs"]) for r in traces),
                "completed_without_adoption": sum(r["completed"] and not any(x["accepted"] for x in r["repairs"]) for r in traces),
                "action_attempts": sum(len(r["actions"]) for r in traces),
                "observed_events": sum(len(a["events"]) for r in traces for a in r["actions"]),
                "action_budget_exhausted": sum(r["action_budget_exhausted"] for r in traces),
                "runtime_backend_calls": session.backend_calls, "repair_backend_calls": repair_calls,
                "total_backend_calls": session.backend_calls+repair_calls,
                "runtime_origins": dict(Counter(r["origin"] for r in bridge.records)),
                "initial_statuses": dict(Counter(r["initial_status"] for r in traces)),
                "observations": dict(Counter(a["observation"] for r in traces for a in r["actions"])), **checks}
            if arm == "no_repair":
                assert metrics["completed"] == 197
            row = {"repeat": repeat, "arm": arm, "prep_ms": (prepared-started)*1000,
                "loop_ms": (finished-prepared)*1000, "total_ms": (finished-started)*1000,
                "semantic_trace_sha256": semantic, "metrics": metrics}
            timings.append(row)
            if repeat == 0:
                first[arm] = traces
                write(args.output/f"{arm}_trace.json", traces)
                write(args.output/f"{arm}_runtime_decisions.json", bridge.records)
            print(json.dumps(row), flush=True)
    methods = {}
    for arm in ARMS:
        rows = [r for r in timings if r["arm"] == arm]
        assert all(r["metrics"] == rows[0]["metrics"] for r in rows)
        methods[arm] = {"metrics": rows[0]["metrics"], "medians": {
            k: median(r[k] for r in rows) for k in ("prep_ms", "loop_ms", "total_ms")}}
    summary = {"episodes": 32, "requests": 384, "methods": methods,
        "four_same_function_arms_semantic_traces_identical": True,
        "reference_unique_snapshots": len(references), "repair_optima_independently_checked": len(repair_refs),
        "physical_execution": False, "model_calls": 0, "target_learning": False,
        "target_cache_growth": False, "source_learning_cost_included": False,
        "timing_scope": "complete_symbolic_supervision_repair_adoption_dispatch_observation_loop"}
    write(args.output/"timings.json", timings)
    write(args.output/"summary.json", summary)
    close(args.output, hashes, {str(p/"manifest.json"): digest(p/"manifest.json") for p in (SOURCE, PARTIAL, FIXTURES)})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
