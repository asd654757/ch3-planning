"""Run the ROUTED pick/place contract over the native soup-to-basket policy.

Uses the same prepared venv and PYTHONPATH as run_routed_pi05_libero.py. Only
the geometric verifier receives privileged simulator measurements.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from ch3.supervision.libero_gate import LiberoTaskGate
from ch3.supervision.libero_semantic import EVIDENCE_SOURCE, LiberoSemanticSupervisor, MujocoSoupObserver
from ch3.supervision.runtime import JsonlJournal
from run_routed_pi05_libero import SEED, SUITE, TASK_ID, hide_wrist, read_same_state


def batch(value):
    return {key: batch(item) for key, item in value.items()} if isinstance(value, dict) else value[None, ...]


def rollout(env, policy, processors, index, condition, emit, refresh):
    import torch
    from lerobot.envs.utils import preprocess_observation
    from lerobot.utils.constants import ACTION

    env_before, before, after, env_after = processors
    base = env.envs[0].unwrapped
    base.init_state_id = index
    for processor in processors:
        processor.reset()
    policy.reset()
    torch.manual_seed(SEED)
    obs, _ = env.reset(seed=SEED)
    wrapper = base._env
    observer = MujocoSoupObserver(wrapper.env)
    gate = LiberoTaskGate(SUITE, TASK_ID, base.task_description)
    emit({"event": "start", "condition": condition, "initial_state_index": index, "seed": SEED,
          "instruction": base.task_description, "evidence_source": EVIDENCE_SOURCE, "refresh_at_pick": refresh})
    supervisor = LiberoSemanticSupervisor(observer.read(), emit)
    skill = supervisor.authorize()
    start = perf_counter()
    sent = predictions = discarded = reobservations = settles = drops = 0
    pick_count = place_count = 0
    queue = []
    reason = "step_budget"
    fault_applied = False
    drop_fault_steps = 0
    sample = observer.read()
    with torch.inference_mode():
        while sent < base._max_episode_steps:
            if gate.observe(obs) != "supported":
                reason = "public_observation_missing"
                break
            ticket = gate.context.issue_request()
            drop_fault = condition == "drop_after_pick" and skill == "place" and drops == 0
            settling = skill == "place" and sample.released_in_basket
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
            emit({"event": "action_applied", "step": sent, "skill": skill, "origin": origin,
                  "chunk": predictions if origin == "pi05" else None, "request_id": ticket.request_id,
                  "requested_action": bounded[0].tolist(), "action": applied[0].tolist()})
            observation_id = f"step-{sent}"
            pick_count = pick_count + 1 if skill == "pick" and sample.supports_pick else 0
            place_count = place_count + 1 if skill == "place" and sample.supports_place else 0
            if pick_count >= 2:
                if condition in {"delayed_pick_evidence", "persistent_pick_evidence_dropout"} and not fault_applied:
                    fault_applied = True
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
                supervisor.confirm_pick(sample, observation_id)
                if refresh:
                    discarded += len(queue)
                    emit({"event": "discard_queue", "after_steps": sent, "actions": len(queue), "reason": "pick_to_place"})
                    queue.clear()
                skill = supervisor.authorize()
            elif place_count >= 2:
                emit({"event": "sample", "id": observation_id, "effect": "place", **asdict(sample)})
                supervisor.confirm_place(sample, observation_id)
                if supervisor.authorize() is not None:
                    raise RuntimeError("the settled placement did not close the task")
                reason = "contract_complete"
                break
            elif skill == "place" and sample.settled_on_floor:
                drops += 1
                emit({"event": "sample", "id": observation_id, "effect": "drop_to_floor", **asdict(sample)})
                supervisor.observe_drop(sample, observation_id)
                if drops > 1:
                    reason = "drop_retry_budget"
                    break
                discarded += len(queue)
                queue.clear()
                skill = supervisor.authorize()
            elif skill == "pick" and sample.released_in_basket:
                reason = "placement_without_confirmed_pick"
                break

    history = [asdict(event) for event in supervisor.runtime.snapshot.history]
    original = [asdict(event) for event in supervisor.runtime.original_history]
    result = {"initial_state_index": index, "condition": condition, "steps": sent,
              "routed_contract_complete": reason == "contract_complete", "native_libero_success": bool(wrapper.check_success()),
              "stop_reason": reason, "policy_calls": predictions, "reobservations": reobservations,
              "discarded_actions": discarded, "settle_steps": settles, "confirmed_drops": drops,
              "injected_drop_fault_steps": drop_fault_steps,
              "original_history": original, "effective_history": history,
              "findings": [asdict(entry) for entry in supervisor.runtime.findings],
              "wall_seconds": round(perf_counter()-start, 3)}
    emit({"event": "summary", **result})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retain-approved-suffix", action="store_true")
    parser.add_argument("--drop-probe", action="store_true", help="Run only the initial state 0 gripper fault probe")
    args = parser.parse_args()

    from lerobot.envs.configs import LiberoEnv
    from lerobot.envs.factory import make_env, make_env_pre_post_processors
    from lerobot.policies.factory import make_pre_post_processors
    from smoke_pi05 import ROOT, load_policy

    args.output.mkdir(parents=True, exist_ok=False)
    checkpoint = ROOT / "checkpoint-local"
    config, policy = load_policy(checkpoint)
    before, after = make_pre_post_processors(policy_cfg=config, pretrained_path=checkpoint)
    env_cfg = LiberoEnv(task=SUITE, task_ids=[TASK_ID], control_mode="relative")
    env_before, env_after = make_env_pre_post_processors(env_cfg, config)
    env = make_env(env_cfg, n_envs=1)[SUITE][TASK_ID]
    refresh = not args.retain_approved_suffix
    runs = ([(0, "drop_after_pick")] if args.drop_probe else
            [(0, "clean"), (1, "clean"), (0, "delayed_pick_evidence"), (0, "persistent_pick_evidence_dropout")])
    summary = {"suite": SUITE, "task_id": TASK_ID, "evidence_source": EVIDENCE_SOURCE,
               "native_scoring_used_only_at_end": True, "refresh_at_pick": refresh,
               "checkpoint_revision": Path((ROOT / "checkpoint_path.txt").read_text().strip()).name,
               "episodes": []}
    try:
        for index, condition in runs:
            with JsonlJournal(args.output / f"state-{index}-{condition}.jsonl") as journal:
                result = rollout(env, policy, (env_before, before, after, env_after), index, condition, journal.emit, refresh)
            summary["episodes"].append(result)
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(result), flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    main()
