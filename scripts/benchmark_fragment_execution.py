"""Prospective development replay of frozen cores inside the dispatch runtime.

Controlled finite-state observations, not physical or RGB evidence. Reference
solving and trace audits happen outside all online method clocks.
"""
import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import random
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.fragments import Atom
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write

TRAINING = ROOT / "fragment_compact_source_20261116_v1"
PROTOCOL = Path("docs/fragment_execution_protocol_20261116.md")


def action_key(event):
    # Predicted provenance is intentionally different from observed provenance.
    return event.seq, event.kind, event.object, event.target


def archive(output):
    files = [Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
             *sorted(Path("ch3/revision").glob("*.py")),
             Path("ch3/schema/model_plan.py"), Path("tests/test_fragment_runtime.py"),
             Path("scripts/benchmark_fragment_three_objects.py")]
    hashes = {}
    for p in files:
        hashes[str(p)] = digest(p)
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def fixtures(output):
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    d = build_compact_domain()
    rng = random.Random(11681)
    terminal = [s for s in d.states if s.holding is None]
    visits = [Atom("visit", o, t) for o in d.model.OBJECTS for t in d.model.TARGETS]
    episodes = []
    for episode in range(32):
        revisions = []
        for revision in range(12):
            while True:
                size = rng.randint(3, 6)
                goal_count = rng.randint(1, 3)
                goal_state = rng.choice(terminal)
                atoms = {Atom("at", o, getattr(goal_state, o))
                         for o in rng.sample(list(d.model.OBJECTS), goal_count)}
                extras = list(visits)
                if rng.random() < .15:
                    extras.append(Atom("forbid", rng.choice(d.model.OBJECTS)))
                if rng.random() < .20:
                    extras.append(Atom("empty"))
                atoms.update(rng.sample(extras, size-len(atoms)))
                if len(atoms) == size and {a.object for a in atoms if a.object} == set(d.model.OBJECTS):
                    break
            revisions.append({"index": revision, "atoms": sorted(atoms),
                "first_action_observation": {1: "no_effect", 2: "foreign_legal_event"}.get(revision % 4, "normal")})
        episodes.append({"id": f"execution-{episode:03}", "revisions": revisions})
    write(output / "episodes.json", episodes)
    write(output / "summary.json", {"seed": 11681, "episodes": 32, "revisions": 384,
        "labels_computed": False, "physical_execution": False, "model_calls": 0})
    close(output, hashes)
    print(json.dumps(read(output / "summary.json")), flush=True)


def run_loop(d, bridge, templates):
    traces = []
    m = d.model
    for episode in templates:
        rt = bridge.Runtime(m.Snapshot(m.Contract(0, ()), m.State(), (), episode["id"]).validate())
        for revision in episode["revisions"]:
            obligations = []
            for i, raw in enumerate(revision["atoms"]):
                clause = m.Clause(id=f"r{revision['index']}-{i}",
                    **{k: v for k, v in raw.items() if v})
                obligations.append(m.Obligation(clause, rt.snapshot.cursor))
            contract = m.Contract(rt.snapshot.contract.version+1, tuple(obligations))
            result, _ = rt.accept(contract, expected_digest=rt.snapshot.digest, expected_generation=rt.generation)
            row = {"episode": episode["id"], "revision": revision["index"],
                   "initial_status": result.status, "decisions": [], "actions": []}
            row["decisions"].append({"snapshot": rt.snapshot, "decision": result})
            while result.status == "ready" and result.plan:
                if len(row["actions"]) >= 20:
                    raise RuntimeError("declared action attempt limit reached")
                requested = result.plan[0]
                before = rt.snapshot
                command = rt.consume(rt.issue())
                assert rt.snapshot is before  # Commands never add evidence.
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
                row["actions"].append({"command": command.model_dump(mode="json"),
                    "observation": observation, "state": state, "events": events})
                result, _ = rt.assess(remaining)
                row["decisions"].append({"snapshot": rt.snapshot, "decision": result})
            row["completed"] = result.status == "ready"
            row["final_status"] = result.status
            row["final_snapshot"] = rt.snapshot
            traces.append(row)
    return traces


def trace_digest(rows, output):
    path = output / "_trace_hash_input.json"
    write(path, rows)
    value = digest(path)
    path.unlink()
    return value


def audit(d, bridge, traces, reference):
    checked = ready = 0
    for row in traces:
        for step in row["decisions"]:
            snapshot, decision = step["snapshot"], step["decision"]
            key = snapshot.digest
            if key not in reference:
                reference[key] = d.model.decide(snapshot, snapshot.contract, 8)
            gold = reference[key]
            assert decision.status == gold.status
            if decision.status == "ready":
                assert len(decision.plan) <= 8 and bridge.valid_suffix(snapshot, snapshot.contract, decision.plan)
                ready += 1
            else:
                assert not decision.plan
                assert (decision.history_alternatives, decision.feasible_alternatives,
                        decision.disputed_events, decision.violated_clauses) == (
                        gold.history_alternatives, gold.feasible_alternatives,
                        gold.disputed_events, gold.violated_clauses)
            checked += 1
        if row["completed"]:
            snap = row["final_snapshot"]
            assert all(d.model.monitor(snap.contract, snap.state, h) == (frozenset(), frozenset())
                       for h in d.model.possible_histories(snap.history))
        if row["initial_status"] != "ready":
            assert not row["actions"]
    return {"decisions_checked": checked, "ready_suffixes_checked": ready,
            "false_conflicts": 0, "unsafe_ready": 0, "unexplained_trace_divergence": 0}


def validation(fixtures_path, output):
    check(fixtures_path)
    check(TRAINING)
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    templates = read(fixtures_path / "episodes.json")
    source_times = read(TRAINING / "timings.json")
    timings, reference = [], {}
    expected_trace = None
    for repeat in range(3):
        for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
            began = perf_counter()
            d = build_compact_domain()
            lower = IndependentActionBound(d)
            bank = d.regions.RegionBank(read(TRAINING / f"{name}_certificates.json"))
            session = d.Session(bank)
            session.memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"]))
                            for r in read(TRAINING / "shared_memo.json")}
            session.freeze()
            bridge = build_fragment_runtime(d, session, lower)
            preparation_ms = (perf_counter()-began)*1000
            cache_size = len(session.memo)
            began = perf_counter()
            traces = run_loop(d, bridge, templates)
            loop_ms = (perf_counter()-began)*1000
            # All reference solving, serialization and cross-arm audits follow clocks.
            checks = audit(d, bridge, traces, reference)
            semantic_digest = trace_digest(traces, output)
            if expected_trace is None:
                expected_trace = semantic_digest
            assert semantic_digest == expected_trace
            assert len(session.memo) == cache_size and not session.patterns
            assert bridge.unchanged_runtime_methods
            source_ms = next(r["preparation_ms"] for r in source_times
                             if r["repeat"] == repeat and r["method"] == name)
            row = {"repeat": repeat, "method": name, "preparation_ms": preparation_ms,
                "execution_loop_ms": loop_ms, "online_total_ms": preparation_ms+loop_ms,
                "source_preparation_ms_from_prior_stage": source_ms,
                "staged_full_cost_estimate_ms": source_ms+preparation_ms+loop_ms,
                "decision_ms": sum(r["elapsed_ms"] for r in bridge.records),
                "backend_calls": session.backend_calls, "origins": Counter(r["origin"] for r in bridge.records),
                "initial_statuses": Counter(r["initial_status"] for r in traces),
                "completed_revisions": sum(r["completed"] for r in traces),
                "suspended_during_execution": sum(r["initial_status"] == "ready" and not r["completed"] for r in traces),
                "action_attempts": sum(len(r["actions"]) for r in traces),
                "observed_events": sum(len(a["events"]) for r in traces for a in r["actions"]),
                "interventions": Counter(a["observation"] for r in traces for a in r["actions"]),
                "semantic_trace_sha256": semantic_digest, **checks}
            if repeat == 0:
                write(output / f"{name}_trace.json", traces)
                write(output / f"{name}_decisions.json", bridge.records)
            timings.append(row)
            write(output / "timings.json", timings)
            print(json.dumps(row), flush=True)
    summary = {"episodes": 32, "revisions": 384, "methods": {}, "physical_execution": False,
        "model_calls": 0, "target_learning": False, "target_cache_growth": False,
        "semantic_traces_identical": True, "reference_unique_snapshots": len(reference),
        "timing_scope": "controlled_symbolic_execution_loop_not_robot_wall_time"}
    metrics = ("decision_ms", "execution_loop_ms", "preparation_ms", "online_total_ms", "staged_full_cost_estimate_ms")
    for name in ("fixed", "learned"):
        rows = [r for r in timings if r["method"] == name]
        summary["methods"][name] = {"medians": {k: median(r[k] for r in rows) for k in metrics},
            **{k: rows[0][k] for k in ("backend_calls", "origins", "initial_statuses", "completed_revisions",
                 "suspended_during_execution", "action_attempts", "observed_events", "interventions",
                 "decisions_checked", "ready_suffixes_checked", "false_conflicts", "unsafe_ready")}}
    a, b = summary["methods"]["fixed"], summary["methods"]["learned"]
    summary["backend_calls_saved"] = a["backend_calls"]-b["backend_calls"]
    summary["reduction_percent"] = {k: (a["medians"][k]-b["medians"][k])/a["medians"][k]*100 for k in metrics}
    summary["paired_online_savings_ms"] = [
        next(r["online_total_ms"] for r in timings if r["repeat"] == i and r["method"] == "fixed") -
        next(r["online_total_ms"] for r in timings if r["repeat"] == i and r["method"] == "learned") for i in range(3)]
    write(output / "summary.json", summary)
    close(output, hashes, {str(fixtures_path / "manifest.json"): digest(fixtures_path / "manifest.json"),
                           str(TRAINING / "manifest.json"): digest(TRAINING / "manifest.json")})
    print(json.dumps(summary), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("fixtures", "validation"), required=True)
    p.add_argument("--fixtures", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.phase == "fixtures":
        fixtures(args.output)
    elif args.fixtures:
        validation(args.fixtures, args.output)
    else:
        p.error("validation requires --fixtures")


if __name__ == "__main__":
    main()
