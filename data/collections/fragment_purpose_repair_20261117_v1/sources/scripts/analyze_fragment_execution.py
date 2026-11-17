"""Independent finite-model, evidence-chain and coverage audit of execution replay."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
from statistics import median

from ch3.revision.compact_fragment_certificates import build_compact_domain
from scripts.analyze_fragment_three_objects import conditions, matches
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write


def decode(d, raw):
    m = d.model
    task = m.Contract(raw["contract"]["version"], tuple(m.Obligation(m.Clause(**o["clause"]), o["since"])
        for o in raw["contract"]["obligations"]))
    return m.Snapshot(task, m.State(**raw["state"]), tuple(m.Event(**e) for e in raw["history"]),
                      raw["observation_id"]).validate()


def action_key(e):
    return e.seq, e.kind, e.object, e.target


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    fixtures = ROOT / "fragment_execution_fixtures_20261116_v1"
    source = ROOT / "fragment_compact_source_20261116_v1"
    target = ROOT / "fragment_execution_validation_20261116_v1"
    for folder in (fixtures, source, target):
        check(folder)
    args.output.mkdir(parents=True, exist_ok=False)
    d = build_compact_domain()
    fixed = read(source / "fixed_certificates.json")
    learned = read(source / "learned_certificates.json")
    old_keys = {c["key"] for c in fixed}
    new = [c for c in learned if c["key"] not in old_keys]
    three_keys = {c["key"] for c in new if sum(t == "object" for _, t in c["pattern"]["variables"]) == 3}
    guards = {"fixed": conditions(d, fixed), "learned": conditions(d, learned),
        "without_new_three": conditions(d, [c for c in learned if c["key"] not in three_keys])}
    traces = read(target / "fixed_trace.json")
    assert traces == read(target / "learned_trace.json")
    records = {name: read(target / f"{name}_decisions.json") for name in ("fixed", "learned")}
    extra, origins = [], {name: Counter() for name in records}
    events_checked = 0
    index = 0
    previous_by_episode = {}
    for row in traces:
        initial = decode(d, row["decisions"][0]["snapshot"])
        previous = previous_by_episode.get(row["episode"])
        if previous is not None:
            assert initial.state == previous.state and initial.history == previous.history
            assert initial.contract.version == previous.contract.version+1
        else:
            assert initial.state == d.model.State() and not initial.history and initial.contract.version == 1
        assert all(o.since == initial.cursor for o in initial.contract.obligations)
        assert len(row["decisions"]) == len(row["actions"])+1
        for j, step in enumerate(row["decisions"]):
            s = decode(d, step["snapshot"])
            result = step["decision"]
            gold = d.model.decide(s, s.contract, 8)
            assert result["status"] == gold.status
            if result["status"] == "ready":
                state = s.state
                plan = tuple(d.model.Event(**e) for e in result["plan"])
                assert len(plan) <= 8
                for k, event in enumerate(plan, 1):
                    assert event.seq == s.cursor+k
                    state = next(ns for ns, e in d.model.successors(state, event.seq)
                                 if action_key(e) == action_key(event))
                assert all(d.model.monitor(s.contract, state, (*h, *plan)) == (frozenset(), frozenset())
                           for h in d.model.possible_histories(s.history))
            else:
                assert not result["plan"]
            mask, state_index = d.features(s)
            for name in records:
                r = records[name][index]
                assert r["status"] == result["status"] and r["cursor"] == s.cursor
                origins[name][r["origin"]] += 1
                if r["origin"] == "certified_core":
                    assert matches(guards[name], mask, state_index)
                    assert r["proof"]["state_index"] == state_index
                    assert r["proof"]["key"] in matches(guards[name], mask, state_index)
            if records["fixed"][index]["origin"] == "backend" and records["learned"][index]["origin"] == "certified_core":
                assert not matches(guards["fixed"], mask, state_index)
                extra.append({"episode": row["episode"], "revision": row["revision"],
                    "decision_index_within_revision": j, "cursor": s.cursor,
                    "stage": "task_revision" if j == 0 else "post_action_recovery",
                    "requires_new_three_object_core": not matches(guards["without_new_three"], mask, state_index),
                    "supporting_new_keys": sorted(matches(guards["learned"], mask, state_index)-old_keys)})
            index += 1
            if j < len(row["actions"]):
                action = row["actions"][j]
                requested = d.model.Event(**result["plan"][0])
                assert action["command"]["skill"] == requested.kind
                assert action["command"]["object_id"] == f"{requested.object}_candidate"
                post = decode(d, row["decisions"][j+1]["snapshot"])
                events = tuple(d.model.Event(**e) for e in action["events"])
                assert post.contract == s.contract and post.history == (*s.history, *events)
                assert post.state == d.model.State(**action["state"])
                if not events:
                    assert action["observation"] in ("no_effect", "foreign_unavailable_no_effect")
                    assert post.state == s.state and post.cursor == s.cursor
                else:
                    assert len(events) == 1 and events[0].seq == s.cursor+1
                    assert events[0].evidence == "confirmed" and events[0].observation_id == post.observation_id
                    assert any(ns == post.state and action_key(e) == action_key(events[0])
                               for ns, e in d.model.successors(s.state, s.cursor+1))
                    if action["observation"] == "normal":
                        assert action_key(events[0]) == action_key(requested)
                    else:
                        assert action["observation"] == "foreign_legal_event"
                        assert action_key(events[0]) != action_key(requested)
                    events_checked += 1
        final = decode(d, row["final_snapshot"])
        assert final == decode(d, row["decisions"][-1]["snapshot"])
        previous_by_episode[row["episode"]] = final
        assert row["completed"] == (row["final_status"] == "ready")
    assert all(len(records[name]) == index for name in records)
    summary = read(target / "summary.json")
    assert len(extra) == summary["backend_calls_saved"]
    timings = read(target / "timings.json")
    paired = {}
    for metric in ("online_total_ms", "staged_full_cost_estimate_ms"):
        paired[metric] = [next(r[metric] for r in timings if r["method"] == "fixed" and r["repeat"] == i)
            - next(r[metric] for r in timings if r["method"] == "learned" and r["repeat"] == i) for i in range(3)]
    for name in records:
        assert origins[name] == Counter(summary["methods"][name]["origins"])
        for metric, expected in summary["methods"][name]["medians"].items():
            assert median(r[metric] for r in timings if r["method"] == name) == expected
    metrics = {"reference_decisions_independently_replayed": index,
        "observed_events_independently_replayed": events_checked,
        "all_cross_revision_ledgers_and_states_continuous": True,
        "all_predictions_separate_from_observed_event_provenance": True,
        "extra_backend_calls_avoided": len(extra), "extra_hits_by_stage": Counter(r["stage"] for r in extra),
        "extra_hits_requiring_new_three_object_core": sum(r["requires_new_three_object_core"] for r in extra),
        "paired_savings_ms": paired, "physical_execution": False, "model_calls": 0}
    write(args.output / "metrics.json", metrics)
    write(args.output / "extra_coverage.json", extra)
    close(args.output, {str(Path(__file__)): digest(__file__),
        "scripts/analyze_fragment_three_objects.py": digest("scripts/analyze_fragment_three_objects.py")},
        {str(f / "manifest.json"): digest(f / "manifest.json") for f in (fixtures, source, target)})
    print(json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
