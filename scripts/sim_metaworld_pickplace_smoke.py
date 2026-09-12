#!/usr/bin/env python3
"""Minimal MetaWorld pick-place smoke test using the bundled expert policy.

This intentionally avoids VLM/API calls.  It checks that the simulation can be
reset, stepped, and evaluated through the underlying MetaWorld environment.
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")

import numpy as np
from lerobot.envs.metaworld import MetaworldEnv


def run_episode(episode: int, max_steps: int, observation_size: int) -> dict:
    env = MetaworldEnv(
        task="metaworld-pick-place-v3",
        observation_width=observation_size,
        observation_height=observation_size,
        obs_type="pixels_agent_pos",
    )
    started = time.time()
    try:
        env.reset()
        raw = env._env._get_obs()
        policy = env.expert_policy

        initial_puck_pos = np.asarray(raw[4:7], dtype=float).tolist()
        target_pos = np.asarray(env._env.unwrapped._target_pos, dtype=float).tolist()
        success = False
        final_info_success = False
        min_puck_target_distance = float("inf")
        last_action = None
        terminated = False
        truncated = False

        for step in range(max_steps):
            action = policy.get_action(raw)
            if action is None:
                break
            action = np.asarray(action, dtype=float)
            last_action = action.tolist()
            _, _, terminated, truncated, info = env._env.step(action)
            raw = env._env._get_obs()

            puck_pos = np.asarray(raw[4:7], dtype=float)
            goal_pos = np.asarray(raw[-3:], dtype=float)
            distance = float(np.linalg.norm(puck_pos - goal_pos))
            min_puck_target_distance = min(min_puck_target_distance, distance)

            # MetaWorld 3.0 exposes success in step info.  We do not access the
            # unstable/unavailable env.success attribute here.
            final_info_success = bool(info.get("success", info.get("is_success", False)))
            if final_info_success:
                success = True
                break
            if terminated or truncated:
                break

        final_puck_pos = np.asarray(raw[4:7], dtype=float).tolist()
        final_goal_pos = np.asarray(raw[-3:], dtype=float).tolist()
        final_distance = float(np.linalg.norm(np.asarray(final_puck_pos) - np.asarray(final_goal_pos)))

        return {
            "record_type": "sim_metaworld_pickplace_smoke",
            "simulator": "metaworld",
            "task": "metaworld-pick-place-v3",
            "episode": episode,
            "success": success,
            "steps": step + 1,
            "elapsed_s": time.time() - started,
            "initial_puck_pos": initial_puck_pos,
            "final_puck_pos": final_puck_pos,
            "target_pos": target_pos,
            "final_goal_obs": final_goal_pos,
            "final_puck_target_distance": final_distance,
            "min_puck_target_distance": min_puck_target_distance,
            "last_action": last_action,
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "info_success": final_info_success,
        }
    finally:
        try:
            env.close()
        except Exception as exc:
            # Some EGL/MuJoCo teardown paths emit harmless destructor errors.
            print(f"[sim] close_warning: {type(exc).__name__}: {exc}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--observation-size", type=int, default=224)
    parser.add_argument("--output-dir", type=Path, default=Path("data/collections"))
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"sim_metaworld_pickplace_smoke_{stamp}.json"

    episodes = []
    for episode in range(args.episodes):
        print(f"[sim] episode={episode + 1}/{args.episodes} start", flush=True)
        result = run_episode(episode, args.max_steps, args.observation_size)
        episodes.append(result)
        print(
            f"[sim] episode={episode + 1}/{args.episodes} "
            f"success={result['success']} steps={result['steps']} "
            f"distance={result['final_puck_target_distance']:.4f}",
            flush=True,
        )

    payload = {
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_type": "sim_metaworld_pickplace_smoke_summary",
        "episodes": args.episodes,
        "max_steps": args.max_steps,
        "successes": sum(e["success"] for e in episodes),
        "success_rate": sum(e["success"] for e in episodes) / args.episodes,
        "episodes_detail": episodes,
        "output": str(output_path),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "episodes_detail"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
