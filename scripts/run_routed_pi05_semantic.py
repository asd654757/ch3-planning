"""Run the ROUTED pick/place contract over the native soup-to-basket policy.

Uses the same prepared venv and PYTHONPATH as run_routed_pi05_libero.py. Only
the geometric verifier receives privileged simulator measurements.
"""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from ch3.supervision.libero_gate import LiberoTaskGate
from ch3.supervision.libero_semantic import EVIDENCE_SOURCE, LiberoSemanticSupervisor, MujocoSoupObserver
from ch3.supervision.runtime import JsonlJournal
from run_routed_pi05_libero import SEED, SUITE, TASK_ID, hide_wrist, read_same_state


ARMS = ("native", "current_observation", "routed")
CONDITIONS = ("clean", "delayed_pick_evidence", "persistent_pick_evidence_dropout", "drop_after_pick")
HELD_OUT_STATES = tuple(range(2, 10))


def comparison_schedule():
    for index in HELD_OUT_STATES:
        for condition_index, condition in enumerate(CONDITIONS):
            order = (index + condition_index) % len(ARMS)
            for arm in (*ARMS[order:], *ARMS[:order]):
                yield index, condition, arm


def summarize_comparison(rows):
    groups = []
    for condition in CONDITIONS:
        for arm in ARMS:
            trials = [r for r in rows if r["arm"] == arm and r["condition"] == condition]
            if trials:
                groups.append({"arm": arm, "condition": condition, "trials": len(trials),
                               "settled_successes": sum(r["independently_settled"] for r in trials),
                               "native_successes": sum(r["native_libero_success"] for r in trials),
                               "missing_evidence_stops": sum(r["stop_reason"] == "unresolved_pick_evidence" for r in trials),
                               "faults_triggered": sum(r["pick_evidence_fault_triggered"]
                                                       or r["injected_drop_fault_steps"] > 0 for r in trials),
                               "verified_drops": sum(r["confirmed_drops"] for r in trials),
                               "policy_actions_during_evidence_loss": sum(r["policy_actions_during_evidence_loss"] for r in trials),
                               "historical_findings": sum(len(r["findings"] or ()) for r in trials),
                               "median_steps": median(r["steps"] for r in trials),
                               "median_steps_on_success": median(r["steps"] for r in trials if r["independently_settled"])
                               if any(r["independently_settled"] for r in trials) else None,
                               "median_policy_calls": median(r["policy_calls"] for r in trials)})
    paired = []
    indexed = {(r["initial_state_index"], r["condition"], r["arm"]): r for r in rows}
    for condition in CONDITIONS:
        for other in ARMS[:-1]:
            matched = [(indexed[index, condition, "routed"], indexed[index, condition, other]) for index in HELD_OUT_STATES
                       if (index, condition, "routed") in indexed and (index, condition, other) in indexed]
            if matched:
                differences = [int(a["independently_settled"]) - int(b["independently_settled"]) for a, b in matched]
                paired.append({"condition": condition, "baseline": other, "matched_states": len(matched),
                               "routed_only": differences.count(1), "baseline_only": differences.count(-1),
                               "same_outcome": differences.count(0),
                               "median_step_delta": median(a["steps"] - b["steps"] for a, b in matched)})
    return {"groups": groups, "paired": paired}


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def audit_comparison(output, schedule, results):
    assert [(r["initial_state_index"], r["condition"], r["arm"]) for r in results] == schedule
    fault_alignment = {}
    for (index, condition, arm), row in zip(schedule, results, strict=True):
        path = output / f"state-{index}-{condition}-{arm}.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert events[-1] == {"event": "summary", **row}
        actions = [event for event in events if event["event"] == "action_applied"]
        assert [action["step"] for action in actions] == list(range(1, row["steps"] + 1))
        anchor = next((event["after_steps"] for event in events if event["event"] == "withhold_pick_evidence"), None)
        if condition == "drop_after_pick":
            anchor = next((action["step"] - 1 for action in actions
                           if action["origin"] == "hold_during_simulated_gripper_fault"), None)
        if condition != "clean":
            fault_alignment.setdefault((index, condition), {})[arm] = anchor
        if arm != "native" and condition == "persistent_pick_evidence_dropout" and anchor is not None:
            assert row["steps"] == anchor and row["stop_reason"] == "unresolved_pick_evidence"
        if arm != "native" and condition == "delayed_pick_evidence" and anchor is not None:
            pause = next(i for i, e in enumerate(events) if e["event"] == "need_observation")
            new_action = next((i for i, e in enumerate(events) if i > pause and e["event"] == "action_applied"), len(events))
            assert any(e["event"] == "reobserve" and e["status"] == "supported" for e in events[pause:new_action])
            if arm == "routed" and new_action < len(events):
                assert any(e["event"] == "resolve_evidence" for e in events[pause:new_action])
        if arm == "routed":
            assert all(e["version"] == 1 for e in events if e["event"] == "task_decision")
            if row["routed_contract_complete"]:
                last = row["effective_history"][-1]
                assert last["kind"] == "place" and last["target"] == "green" and last["evidence"] == "confirmed"
    return {"verified_episodes": len(results),
            "fault_anchors": [{"state": i, "condition": c, "steps_before_fault": anchors,
                              "all_triggered_at_same_step": None not in anchors.values() and len(set(anchors.values())) == 1}
                             for (i, c), anchors in fault_alignment.items()]}


def batch(value):
    return {key: batch(item) for key, item in value.items()} if isinstance(value, dict) else value[None, ...]


class CurrentObservationSupervisor:
    """Strong comparison: same sensors and phase rules, with no event ledger."""

    def __init__(self, initial_sample, emit):
        if not initial_sample.settled_on_floor:
            raise ValueError("initial soup can is outside the registered pickup condition")
        self.next_skill, self.emit = "pick", emit

    def authorize(self):
        if self.next_skill is not None:
            self.emit({"event": "authorize_phase", "skill": self.next_skill, "basis": "current_evidence"})
        return self.next_skill

    def await_pick_evidence(self, observation_id):
        self.next_skill = None
        self.emit({"event": "need_observation", "observation_id": observation_id})

    def confirm_pick(self, sample, observation_id):
        if not sample.supports_pick:
            raise ValueError("current evidence does not support holding")
        self.next_skill = "place"

    def confirm_place(self, sample, observation_id):
        if not sample.supports_place:
            raise ValueError("current evidence does not support settled placement")
        self.next_skill = None

    def observe_drop(self, sample, observation_id):
        if not sample.settled_on_floor:
            raise ValueError("current evidence does not support a settled drop")
        self.next_skill = "pick"


def rollout(env, policy, processors, index, condition, emit, refresh, *, arm="routed"):
    import torch
    from lerobot.envs.utils import preprocess_observation
    from lerobot.utils.constants import ACTION

    env_before, before, after, env_after = processors
    base = env.envs[0].unwrapped
    if arm not in ARMS or condition not in CONDITIONS:
        raise ValueError("unknown comparison arm or condition")
    if base._init_states is None or not 0 <= index < len(base._init_states):
        raise ValueError(f"missing fixed LIBERO initial state {index}")
    base.init_state_id = index
    for processor in processors:
        processor.reset()
    policy.reset()
    torch.manual_seed(SEED)
    obs, _ = env.reset(seed=SEED)
    wrapper = base._env
    observer = MujocoSoupObserver(wrapper.env)
    gate = LiberoTaskGate(SUITE, TASK_ID, base.task_description)
    native = arm == "native"
    emit({"event": "start", "condition": condition, "initial_state_index": index, "seed": SEED,
          "instruction": base.task_description, "evidence_source": EVIDENCE_SOURCE, "refresh_at_pick": refresh,
          "arm": arm})
    supervisor = None if native else (LiberoSemanticSupervisor if arm == "routed" else
                                      CurrentObservationSupervisor)(observer.read(), emit)
    skill = "pick" if native else supervisor.authorize()
    start = perf_counter()
    sent = predictions = discarded = reobservations = settles = drops = 0
    pick_count = place_count = 0
    queue = []
    reason = "step_budget"
    fault_applied = False
    native_steps_during_evidence_loss = 0
    drop_fault_steps = 0
    sample = observer.read()
    with torch.inference_mode():
        while sent < base._max_episode_steps:
            if native:
                ticket = None
            else:
                if gate.observe(obs) != "supported":
                    reason = "public_observation_missing"
                    break
                ticket = gate.context.issue_request()
            drop_fault = condition == "drop_after_pick" and skill == "place" and drops == 0
            settling = (native or skill == "place") and sample.released_in_basket
            if drop_fault:
                if drop_fault_steps >= 24:
                    reason = "injected_drop_not_observed"
                    break
                drop_fault_steps += 1
                action = np.zeros((1, 7), dtype=np.float32)
                origin = "hold_during_simulated_gripper_fault"
            elif settling:
                if settles >= 16:
                    reason = "settlement_budget"
                    break
                if queue:
                    discarded += len(queue)
                    emit({"event": "discard_queue", "after_steps": sent, "actions": len(queue), "reason": "settling"})
                    queue.clear()
                settles += 1
                action = np.zeros((1, 7), dtype=np.float32)
                origin = "settle_zero_delta"
            else:
                if not queue:
                    inputs = preprocess_observation(obs)
                    inputs["task"] = [base.task_description]
                    queue = list(policy.predict_action_chunk(before(env_before(inputs)))[:, :policy.config.n_action_steps]
                                 .transpose(0, 1))
                    predictions += 1
                    emit({"event": "predict_chunk", "chunk": predictions, "skill": skill, "after_steps": sent})
                action = env_after({ACTION: after(queue.pop(0))})[ACTION].cpu().numpy()
                origin = "pi05"
            if native:
                if action.shape != (1, 7) or not np.isfinite(action).all():
                    raise ValueError("Pi05 produced an invalid LIBERO action")
                bounded = np.clip(action, env.single_action_space.low, env.single_action_space.high)
            else:
                bounded = gate.dispatch(ticket, action, env.single_action_space)
            # This evaluation-only actuator fault opens the fingers despite
            # the routed zero-delta hold request. Record the actual command
            # separately and use observations to decide whether it dropped.
            applied = bounded.copy()
            if drop_fault:
                applied[0, 6] = -1.0
            # Bypass LeRobot's wrapper auto-reset so terminal physics can be
            # observed after the official completion flag first becomes true.
            raw, _, _, _ = wrapper.step(applied[0])
            obs = batch(base._format_raw_obs(raw))
            sent += 1
            sample = observer.read()
            if (native and fault_applied and origin == "pi05"
                    and (condition == "persistent_pick_evidence_dropout" or native_steps_during_evidence_loss == 0)):
                native_steps_during_evidence_loss += 1
            emit({"event": "action_applied", "step": sent, "skill": skill, "origin": origin,
                  "chunk": predictions if origin == "pi05" else None, "request_id": ticket.request_id if ticket else None,
                  "requested_action": bounded[0].tolist(), "action": applied[0].tolist()})
            observation_id = f"step-{sent}"
            pick_count = pick_count + 1 if skill == "pick" and sample.supports_pick else 0
            place_count = place_count + 1 if (native or skill == "place") and sample.supports_place else 0
            if pick_count >= 2:
                if condition in {"delayed_pick_evidence", "persistent_pick_evidence_dropout"} and not fault_applied:
                    fault_applied = True
                    emit({"event": "withhold_pick_evidence", "after_steps": sent, "condition": condition})
                    if native:
                        skill = "place"  # External fault/scoring phase only, never a policy input.
                        continue
                    gate.observe(hide_wrist(obs))
                    supervisor.await_pick_evidence(f"{observation_id}-missing")
                    discarded += len(queue)
                    emit({"event": "discard_queue", "after_steps": sent, "actions": len(queue), "reason": "pick_evidence_missing"})
                    queue.clear()
                    used = 0
                    while gate.route(used) == "REOBSERVE":
                        fresh = read_same_state(base)
                        used += 1
                        reobservations += 1
                        obs = hide_wrist(fresh) if condition == "persistent_pick_evidence_dropout" else fresh
                        evidence = gate.observe(obs)
                        emit({"event": "reobserve", "after_steps": sent, "attempt": used, "status": evidence})
                    if gate.route(used) != "CONTINUE":
                        reason = "unresolved_pick_evidence"
                        break
                    sample = observer.read()
                    if not sample.supports_pick:
                        reason = "pick_evidence_inconclusive"
                        break
                    observation_id += "-refresh"
                emit({"event": "sample", "id": observation_id, "effect": "pick", **asdict(sample)})
                if native:
                    skill = "place"
                else:
                    supervisor.confirm_pick(sample, observation_id)
                    if refresh:
                        discarded += len(queue)
                        emit({"event": "discard_queue", "after_steps": sent, "actions": len(queue), "reason": "pick_to_place"})
                        queue.clear()
                    skill = supervisor.authorize()
            elif place_count >= 2:
                emit({"event": "sample", "id": observation_id, "effect": "place", **asdict(sample)})
                if not native:
                    supervisor.confirm_place(sample, observation_id)
                    if supervisor.authorize() is not None:
                        raise RuntimeError("the settled placement did not close the task")
                reason = "settled_placement" if native else "contract_complete" if arm == "routed" else "current_goal_confirmed"
                break
            elif skill == "place" and sample.settled_on_floor:
                drops += 1
                emit({"event": "sample", "id": observation_id, "effect": "drop_to_floor", **asdict(sample)})
                if native:
                    skill = "pick"  # Track the outcome for fault injection only.
                else:
                    supervisor.observe_drop(sample, observation_id)
                    if drops > 1:
                        reason = "drop_retry_budget"
                        break
                    discarded += len(queue)
                    queue.clear()
                    skill = supervisor.authorize()
            elif not native and skill == "pick" and sample.released_in_basket:
                reason = "placement_without_confirmed_pick"
                break

    runtime = supervisor.runtime if arm == "routed" else None
    history = [asdict(event) for event in runtime.snapshot.history] if runtime else None
    original = [asdict(event) for event in runtime.original_history] if runtime else None
    result = {"initial_state_index": index, "condition": condition, "arm": arm, "steps": sent,
              "routed_contract_complete": reason == "contract_complete", "native_libero_success": bool(wrapper.check_success()),
              "independently_settled": place_count >= 2,
              "pick_evidence_fault_triggered": fault_applied,
              "policy_actions_during_evidence_loss": native_steps_during_evidence_loss,
              "stop_reason": reason, "policy_calls": predictions, "reobservations": reobservations,
              "discarded_actions": discarded, "settle_steps": settles, "confirmed_drops": drops,
              "injected_drop_fault_steps": drop_fault_steps,
              "original_history": original, "effective_history": history,
              "findings": [asdict(entry) for entry in runtime.findings] if runtime else None,
              "wall_seconds": round(perf_counter()-start, 3)}
    emit({"event": "summary", **result})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retain-approved-suffix", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--drop-probe", action="store_true", help="Run only the initial state 0 gripper fault probe")
    mode.add_argument("--comparison", action="store_true", help="Run the frozen three-arm held-out comparison")
    args = parser.parse_args()
    if args.comparison and args.retain_approved_suffix:
        parser.error("the fixed comparison refreshes both supervised arms after grasp")

    from lerobot.envs.configs import LiberoEnv
    from lerobot.envs.factory import make_env, make_env_pre_post_processors
    from lerobot.policies.factory import make_pre_post_processors
    from smoke_pi05 import ROOT, load_policy

    args.output.mkdir(parents=True, exist_ok=False)
    runs = (list(comparison_schedule()) if args.comparison else
            [(0, "drop_after_pick", "routed")] if args.drop_probe else
            [(0, "clean", "routed"), (1, "clean", "routed"), (0, "delayed_pick_evidence", "routed"),
             (0, "persistent_pick_evidence_dropout", "routed")])
    if args.comparison:
        src = Path(__file__).resolve()
        project = src.parents[1]
        relevant = (src, project / "ch3/supervision/libero_semantic.py", project / "ch3/supervision/libero_gate.py",
                    project / "ch3/revision/evidence_resolution.py", project / "ch3/revision/runtime.py")
        write_json(args.output / "protocol.json", {
            "task": [SUITE, TASK_ID], "seed": SEED, "development_initial_states": [0, 1],
            "manifest": [{"state": i, "condition": c, "arm": a} for i, c, a in runs],
            "primary_outcomes": ["independently_settled", "native_libero_success", "unresolved_pick_evidence"],
            "native_arm": "unmodified full-task policy chunking with the shared evaluator's settling control",
            "current_observation_arm": "same phase predicates, inputs, reobservations, and drop budget as ROUTED; no ledger",
            "fault_anchor": "first two samples supporting lifted bilateral grasp; withheld supervision evidence never removes the policy's RGB input",
            "control_scope": "all arms share finite-step limits, end scoring, actuator fault and stable placement test",
            "goal_evidence_input": EVIDENCE_SOURCE,
            "artifact_hashes": {str(p.relative_to(project)): sha256(p.read_bytes()).hexdigest() for p in relevant},
        })
    checkpoint = ROOT / "checkpoint-local"
    config, policy = load_policy(checkpoint)
    before, after = make_pre_post_processors(policy_cfg=config, pretrained_path=checkpoint)
    env_cfg = LiberoEnv(task=SUITE, task_ids=[TASK_ID], control_mode="relative")
    env_before, env_after = make_env_pre_post_processors(env_cfg, config)
    env = make_env(env_cfg, n_envs=1)[SUITE][TASK_ID]
    refresh = not args.retain_approved_suffix
    summary = {"suite": SUITE, "task_id": TASK_ID, "evidence_source": EVIDENCE_SOURCE,
               "native_scoring_used_only_at_end": True, "refresh_at_pick": refresh,
               "checkpoint_revision": Path((ROOT / "checkpoint_path.txt").read_text().strip()).name,
               "expected_episodes": len(runs), "episodes": [], "complete": False}
    write_json(args.output / "summary.json", summary)
    try:
        for index, condition, arm in runs:
            suffix = f"-{arm}" if args.comparison else ""
            with JsonlJournal(args.output / f"state-{index}-{condition}{suffix}.jsonl") as journal:
                result = rollout(env, policy, (env_before, before, after, env_after), index, condition, journal.emit, refresh, arm=arm)
            summary["episodes"].append(result)
            write_json(args.output / "summary.json", summary)
            print(json.dumps(result), flush=True)
        if args.comparison:
            write_json(args.output / "audit.json", audit_comparison(args.output, runs, summary["episodes"]))
            write_json(args.output / "comparison.json", summarize_comparison(summary["episodes"]))
        summary["complete"] = True
        write_json(args.output / "summary.json", summary)
    finally:
        env.close()


if __name__ == "__main__":
    main()
