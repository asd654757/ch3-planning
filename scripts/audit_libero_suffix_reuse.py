"""Compare suffix validation with same-history replanning on LIBERO phase logs."""

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from ch3.revision.history import Event, decide, monitor, possible_histories
from ch3.supervision.libero_semantic import LiberoSemanticSupervisor, ManipulationSample


def actions(plan):
    return [(step.kind, step.object, step.target) for step in plan]


def audit_episode(source):
    events = [json.loads(line) for line in source.read_text().splitlines()]
    summary = events[-1]
    assert summary["event"] == "summary"
    # The fixed-domain supervisor requires a supported initial floor sample.
    # The source rollout already checked it independently at admission.
    replay_decisions = []

    def record(row):
        if row["event"] == "task_decision":
            replay_decisions.append(row)

    supervisor = LiberoSemanticSupervisor(ManipulationSample(0, False, False, False, False, 0), record)
    actual_origins = []
    unconditional_calls = goal_checked_calls = 0
    for row in events:
        kind = row["event"]
        if kind == "authorize_phase":
            assert supervisor.authorize() == row["skill"]
        elif kind == "need_observation":
            event = Event(**row["disputed_event"])
            supervisor.await_pick_evidence(event.observation_id)
            assert supervisor.disputed == event
        elif kind == "sample":
            sample = ManipulationSample(**{key: row[key] for key in ManipulationSample.__dataclass_fields__})
            commit = {"pick": supervisor.confirm_pick, "place": supervisor.confirm_place,
                      "drop_to_floor": supervisor.observe_drop}[row["effect"]]
            commit(sample, row["id"])
        elif kind == "task_decision":
            assert replay_decisions[len(actual_origins)] == row
            snapshot = supervisor.runtime.snapshot
            actual = supervisor.decision
            gold = decide(snapshot, snapshot.contract, supervisor.runtime.horizon)
            # The reused plan marks its events as predictions. Compare the actions executed.
            assert (actual.status, actions(actual.plan)) == (gold.status, actions(gold.plan)), (source, row)
            already_done = all(monitor(snapshot.contract, snapshot.state, h) == (frozenset(), frozenset())
                               for h in possible_histories(snapshot.history))
            unconditional_calls += 1
            goal_checked_calls += not already_done
            actual_origins.append(row["route"])
    assert len(replay_decisions) == len(actual_origins)
    assert [asdict(event) for event in supervisor.runtime.snapshot.history] == summary["effective_history"]
    assert set(actual_origins) <= {"BOUNDED_REPLAN", "REUSE_VALID_SUFFIX", "SUSPEND"}
    return {"state": summary["initial_state_index"], "condition": summary["condition"],
            "comparison": {"routed_backend_calls": sum(route != "REUSE_VALID_SUFFIX" for route in actual_origins),
                           "unconditional_direct_calls": unconditional_calls, "goal_checked_direct_calls": goal_checked_calls},
            "routes": actual_origins, "trace_sha256": sha256(source.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs = sorted(args.source.glob("state-*-routed.jsonl"))
    assert len(inputs) == 32
    rows = [audit_episode(path) for path in inputs]
    totals = Counter()
    for row in rows:
        totals.update(row["comparison"])
    result = {"audited_episodes": len(rows), "decisions_and_suffixes_match": True, "scope": "same-history replay, no new actuation",
              "totals": dict(totals), "episodes": rows}
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "episodes"}))


if __name__ == "__main__":
    main()
