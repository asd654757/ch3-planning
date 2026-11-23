"""Task-level ROUTED on the locally prepared LeRobot Pi05/LIBERO setup.

Put the repository and local installation directory on PYTHONPATH, set the
local LIBERO_CONFIG_PATH and EGL variables, and use the installation's venv.
The simulator status is read only to delimit the episode and score the run.
"""

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from ch3.supervision.libero_gate import LiberoTaskGate
from ch3.supervision.runtime import JsonlJournal


SUITE, TASK_ID, SEED = "libero_object", 0, 7
FAULT_AT = 25


def read_same_state(base):
    """Re-render both cameras and refresh robot observations without env.step."""
    wrapper = base._env
    sim_time = wrapper.env.sim.data.time
    reset_cursor = base.init_state_id
    raw = wrapper.env._get_observations(force_update=True)
    formatted = base._format_raw_obs(raw)
    assert wrapper.env.sim.data.time == sim_time and base.init_state_id == reset_cursor

    def batch(value):
        return {key: batch(item) for key, item in value.items()} if isinstance(value, dict) else value[None, ...]

    return batch(formatted)


def hide_wrist(observation):
    return {**observation, "pixels": {key: value for key, value in observation["pixels"].items() if key != "image2"}}


def rollout(env, policy, processors, index, condition, emit):
    import torch
    from lerobot.envs.utils import preprocess_observation
    from lerobot.utils.constants import ACTION

    env_before, before, after, env_after = processors
    base = env.envs[0].unwrapped
    description = base.task_description
    gate = LiberoTaskGate(SUITE, TASK_ID, description)
    base.init_state_id = index
    policy.reset()
    for processor in processors:
        processor.reset()
    torch.manual_seed(SEED)
    obs, _ = env.reset(seed=SEED)
    budget = base._max_episode_steps
    emit({"event": "start", "instruction": description, "initial_state_index": index,
          "condition": condition, "seed": SEED, "step_limit": budget})
    generated = discarded = sent = reobservations = clipped = predictions = 0
    chunk = None
    blocked_at = None
    success = False
    reason = "step_budget"
    started = perf_counter()
    with torch.inference_mode():
        while sent < budget:
            fault = sent == FAULT_AT and condition != "clean"
            available = hide_wrist(obs) if fault else obs
            gate.observe(available)
            used = 0
            if gate.route(used) == "REOBSERVE":
                blocked_at = sent
                discarded += generated
                emit({"event": "pause", "after_steps": sent, "discarded_actions": generated,
                      "observation_version": gate.context.observation_version})
                chunk = None
                generated = 0
            while gate.route(used) == "REOBSERVE":
                fresh = read_same_state(base)
                available = hide_wrist(fresh) if condition == "persistent_wrist_dropout" else fresh
                used += 1
                reobservations += 1
                evidence = gate.observe(available)
                emit({"event": "reobserve", "after_steps": sent, "attempt": used, "status": evidence,
                      "observation_version": gate.context.observation_version})
            if gate.route(used) != "CONTINUE":
                reason = "observation_budget_exhausted"
                break

            ticket = gate.context.issue_request()
            if generated == 0:
                inputs = preprocess_observation(available)
                inputs["task"] = [description]
                chunk = iter(policy.predict_action_chunk(before(env_before(inputs)))[:, : policy.config.n_action_steps]
                             .transpose(0, 1))
                generated = policy.config.n_action_steps
                predictions += 1
                emit({"event": "predict_chunk", "chunk": predictions, "after_steps": sent,
                      "observation_version": ticket.observation_version})
            action = env_after({ACTION: after(next(chunk))})[ACTION].cpu().numpy()
            bounded = gate.dispatch(ticket, action, env.single_action_space)
            clipped += bool(np.any(bounded != action))
            generated -= 1
            obs, _, terminated, truncated, info = env.step(bounded)
            sent += 1
            emit({"event": "action_applied", "step": sent, "chunk": predictions,
                  "observation_version": ticket.observation_version, "request_id": ticket.request_id,
                  "policy_action": action[0].tolist(), "applied_action": bounded[0].tolist()})
            if generated == 0 or terminated[0] or truncated[0]:
                gate.context.record_execution(event_id=f"chunk-{predictions}", skill="libero_task_policy",
                                              object_id="alphabet_soup", outcome="unknown",
                                              evidence_source="environment_step_receipt")
            if terminated[0] or truncated[0]:
                final_info = info.get("final_info")
                if isinstance(final_info, dict) and "is_success" in final_info:
                    info = final_info
                success = bool(info["is_success"][0])
                reason = "environment_terminated" if terminated[0] else "environment_truncated"
                break
    result = {"initial_state_index": index, "condition": condition, "steps": sent,
              "stop_reason": reason, "environment_task_success": success, "supervisor_goal_verdict": "unknown",
              "policy_calls": predictions, "paused_after_steps": blocked_at, "reobservations": reobservations,
              "discarded_actions": discarded, "bounded_actions": clipped, "wall_seconds": round(perf_counter()-started, 3)}
    emit({"event": "summary", **result})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
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
    runs = [(0, "clean"), (1, "clean"), (0, "one_frame_wrist_dropout"), (0, "persistent_wrist_dropout")]
    summary = {"suite": SUITE, "task_id": TASK_ID, "checkpoint": checkpoint.resolve().as_posix(),
               "checkpoint_revision": Path((ROOT / "checkpoint_path.txt").read_text().strip()).name,
               "actions_per_prediction": config.n_action_steps, "fault_injection_after_steps": FAULT_AT,
               "goal_scoring": "native LIBERO after termination; never a gate input", "episodes": []}
    try:
        for index, condition in runs:
            with JsonlJournal(args.output / f"state-{index}-{condition}.jsonl") as journal:
                result = rollout(env, policy, (env_before, before, after, env_after), index, condition, journal.emit)
            summary["episodes"].append(result)
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(result), flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    main()
