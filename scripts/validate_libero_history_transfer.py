"""Decision replay over measured LIBERO histories and matched current scenes."""

from argparse import ArgumentParser
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.evidence_resolution import EvidenceFinding, EvidenceRevisionRuntime
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_runtime import build_fragment_runtime
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State, decide
from ch3.supervision.libero_semantic import MujocoSoupObserver
from scripts.benchmark_fragment_purpose_repair import build_bank
from scripts.benchmark_fragment_three_objects import check
from scripts.benchmark_fragment_purpose_repair import SOURCE, PARTIAL


ARMS = ("standard", "fixed", "source_partial", "learned")
STATES = tuple(range(2, 10))


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def trace_observations(suite, index, path):
    from lerobot.envs.libero import LiberoEnv

    actions = [row for line in path.read_text().splitlines() if (row := json.loads(line))["event"] == "action_applied"]
    base = LiberoEnv(task_suite=suite, task_id=0, task_suite_name="libero_object",
                     obs_type="pixels_agent_pos", episode_index=index, observation_width=360, observation_height=360)
    try:
        base.reset(seed=7)
        wrapper = base._env
        observer = MujocoSoupObserver(wrapper.env)
        held_at = returned_at = settled_at = None
        held_run = returned_run = settled_run = 0
        floor_sample = held_sample = None
        raw = None
        for expected, action in enumerate(actions, 1):
            assert action["step"] == expected
            raw, _, _, _ = wrapper.step(np.asarray(action["action"], dtype=np.float32))
            sample = observer.read()
            floor_sample = floor_sample or (asdict(sample) if sample.settled_on_floor else None)
            held_run = held_run + 1 if sample.supports_pick else 0
            settled_run = settled_run + 1 if sample.supports_place else 0
            if held_at is None and held_run >= 2:
                held_at, held_sample = expected, asdict(sample)
            returned_run = returned_run + 1 if held_at is not None and sample.settled_on_floor else 0
            if returned_at is None and returned_run >= 2:
                returned_at = expected
            if settled_at is None and settled_run >= 2:
                settled_at = expected
        final = base._format_raw_obs(raw)
        digest = sha256()
        for key in ("image", "image2"):
            digest.update(final["pixels"][key].tobytes())
        for value in (final["robot_state"]["eef"]["pos"], final["robot_state"]["eef"]["quat"],
                      final["robot_state"]["gripper"]["qpos"]):
            digest.update(value.tobytes())
        return {"actions_replayed": len(actions), "first_held_step": held_at, "returned_to_floor_step": returned_at,
                "settled_in_basket_step": settled_at, "current_observation_digest": digest.hexdigest(),
                "final_sample": asdict(sample), "floor_observed": floor_sample is not None, "held_observed": held_sample is not None,
                "source_sha256": sha(path)}
    finally:
        base.close()


def clauses(model, specs):
    return tuple(model.Obligation(model.Clause(**values), since) for values, since in specs)


def historical_check(current_id, truth, hidden):
    state = State(blue="green")
    expected = "ready" if truth else "conflict"
    observed = Event(1, "place", "blue", "table", "unknown" if hidden else
                     "confirmed" if truth else "refuted", "archived-return-check")
    original = Snapshot(Contract(0, ()), state, (observed,), current_id)
    specs = (({"id": "past_return", "kind": "visit", "object": "blue", "target": "table"}, 0),
             ({"id": "delivered", "kind": "at", "object": "blue", "target": "green"}, 0),
             ({"id": "leave_delivered_can", "kind": "forbid", "object": "blue"}, 1))
    from ch3.revision import history as model

    task = Contract(1, clauses(model, specs))
    runtime = EvidenceRevisionRuntime(original)
    result, _ = runtime.accept(task, expected_digest=original.digest, expected_generation=runtime.generation)
    direct_status = decide(runtime.snapshot, task).status
    assert result.status == direct_status == ("need_observation" if hidden else expected)
    assert not result.plan
    if hidden:
        try:
            runtime.issue()
        except ValueError:
            pass
        else:
            raise AssertionError("a historical dispute allowed dispatch")
        finding = EvidenceFinding(1, "confirmed" if truth else "refuted", "archive-recovered",
                                  runtime.snapshot.digest, runtime.generation)
        runtime.resolve_evidence(finding)
        final, _ = runtime.assess()
        assert runtime.original_history == (observed,) and runtime.snapshot.contract == task
    else:
        final = result
    assert final.status == expected and not final.plan
    assert decide(runtime.snapshot, task).status == expected
    return {"current_observation_digest": current_id, "archived_return": truth, "initially_hidden": hidden,
            "expected": expected, "routed_initial": result.status, "same_history_direct_initial": direct_status,
            "routed_final": final.status, "findings": [asdict(f) for f in runtime.findings],
            "current_only_accept": "ready", "current_only_reject": "conflict", "current_only_defer": "need_observation",
            "contract": {"version": task.version, "obligations": [
                {"clause": obligation.clause.model_dump(), "since": obligation.since} for obligation in task.obligations
            ]}}


def bank_replay(observations):
    for path in (SOURCE, PARTIAL):
        check(path)
    # Identical request matrix for all arms, projected onto registered slots.
    # No physical actuation: several hypothetical requests need skills that
    # this Pi05 checkpoint has not been admitted to execute separately.
    requests = (
        ("deliver", (("at", "green"),)),
        ("leave_untouched", (("at", "green"), ("forbid", None))),
        ("return_then_deliver", (("visit", "table"), ("at", "green"))),
        ("return_without_touching", (("visit", "table"), ("at", "green"), ("forbid", None))),
    )
    expected = None
    report = {}
    for arm in ARMS:
        domain = build_compact_domain()
        model = domain.model
        session = domain.Session(build_bank(domain, arm))
        session.freeze()
        bridge = build_fragment_runtime(domain, session, IndependentActionBound(domain))
        outcomes = []
        for record in observations:
            index = record["state"]
            stages = {"table": record["clean"]["floor_observed"], "hand": record["clean"]["held_observed"],
                      "green": record["clean"]["settled_in_basket_step"] is not None}
            for location, available in stages.items():
                if not available:
                    continue
                state = model.State(blue=location, holding="blue" if location == "hand" else None)
                for request, terms in requests:
                    raw = [({"id": f"requirement-{i}", "kind": kind, "object": "blue", **({"target": target} if target else {})}, 0)
                           for i, (kind, target) in enumerate(terms)]
                    contract = model.Contract(1, clauses(model, raw))
                    snapshot = model.Snapshot(model.Contract(0, ()), state, (), f"libero-{index}-{location}")
                    runtime = bridge.Runtime(snapshot)
                    result, route = runtime.accept(contract, expected_digest=snapshot.digest, expected_generation=0)
                    ref = model.decide(runtime.snapshot, contract, 8)
                    assert result.status == ref.status and result.plan == ref.plan
                    outcomes.append((index, location, request, result.status))
        if expected is None:
            expected = outcomes
        assert outcomes == expected
        report[arm] = {"requests": len(outcomes), "backend_calls": session.backend_calls,
                       "origins": dict(Counter(row["origin"] for row in bridge.records))}
    return report


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--measured", type=Path, help="Reuse an earlier archive of measured MuJoCo replays")
    args = parser.parse_args()

    assert read(args.source / "summary.json")["complete"]
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output / "protocol.json", {"initial_states": STATES, "source": str(args.source),
          "source_summary_sha256": sha(args.source / "summary.json"), "script_sha256": sha(Path(__file__)),
          "reused_measured_sha256": sha(args.measured) if args.measured else None,
          "history_design": "Replay clean and disturbed Pi05 actions in MuJoCo, then pair actual historical prefixes with the very same clean terminal observation in each case. This is an explicit common-state decision replay, not a continuous new policy rollout.",
          "history_window": "The first 20 steps after independently measured pickup. A floor return needs two consecutive settling samples.",
          "scope": "Simulated contact and geometry; no new model inference or physical dispatch."})
    if args.measured:
        replays = read(args.measured)
        assert [row["state"] for row in replays] == list(STATES)
        for row in replays:
            assert row["clean"]["source_sha256"] == sha(args.source / f"state-{row['state']}-clean-routed.jsonl")
            assert row["drop"]["source_sha256"] == sha(args.source / f"state-{row['state']}-drop_after_pick-routed.jsonl")
    else:
        from lerobot.envs.libero import benchmark

        suite = benchmark.get_benchmark_dict()["libero_object"]()
        replays = [{"state": index,
                    "clean": trace_observations(suite, index, args.source / f"state-{index}-clean-routed.jsonl"),
                    "drop": trace_observations(suite, index, args.source / f"state-{index}-drop_after_pick-routed.jsonl")}
                   for index in STATES]
    write(args.output / "measured_replays.json", replays)
    tests, excluded = [], []
    for row in replays:
        index, clean, drop = row["state"], row["clean"], row["drop"]
        reasons = []
        if clean["settled_in_basket_step"] is None:
            reasons.append("missing clean terminal basket observation")
        if clean["first_held_step"] is None or drop["first_held_step"] is None:
            reasons.append("missing supported pickup before inspection window")
        if reasons:
            excluded.append({"state": index, "reasons": reasons})
            continue
        def returned_during_window(trace):
            deadline = trace["first_held_step"] + 20
            assert trace["actions_replayed"] >= deadline, "incomplete archived observation window"
            return trace["returned_to_floor_step"] is not None and trace["returned_to_floor_step"] <= deadline

        for origin, trace in (("clean", clean), ("drop", drop)):
            truth = returned_during_window(trace)
            for hidden in (False, True):
                tests.append({"state": index, "prefix": origin, **historical_check(clean["current_observation_digest"], truth, hidden)})
    counts = Counter((row["prefix"], row["routed_initial"], row["routed_final"]) for row in tests)
    joined = 0
    for index in STATES:
        pair = [row for row in tests if row["state"] == index and not row["initially_hidden"]]
        if len(pair) == 2 and pair[0]["expected"] != pair[1]["expected"]:
            assert pair[0]["current_observation_digest"] == pair[1]["current_observation_digest"]
            assert pair[0]["contract"] == pair[1]["contract"]
            joined += 1
    result = {"action_replays": len(replays)*2, "excluded": excluded, "same_observation_opposite_decision_pairs": joined,
              "decision_cases": len(tests), "withheld_evidence_cases": sum(row["initially_hidden"] for row in tests),
              "correct_initial_routed": sum(row["routed_initial"] == ("need_observation" if row["initially_hidden"] else row["expected"]) for row in tests),
              "correct_after_archive_recovery": sum(row["routed_final"] == row["expected"] for row in tests if row["initially_hidden"]),
              "current_only_best_definitive_decisions_on_opposite_pairs": [joined, 2*joined],
              "outcomes": [{"prefix": p, "initial": a, "final": b, "count": n} for (p, a, b), n in sorted(counts.items())],
              "bank_replay": bank_replay(replays)}
    write(args.output / "history_cases.json", tests)
    write(args.output / "summary.json", result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
