"""Closed-fixture joint evidence -> actual regrasp, not a lookahead comparison."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.execution.recovery_contact_evidence import aperture_evidence, regrasp_gate
from ch3.execution.visual_holding import failed_pick_evidence
from ch3.repair.budgeted_recovery_fixture import RecoveryFixture


def diagnose(fixture):
    fixture.phase = "closed_fixture_diagnosis"
    fixture.grip = 1.
    samples = []
    for _ in range(30):
        fixture.env.step(fixture.np.array([0., 0., 0., 1.]))
        samples.append({"commanded_gripper": 1,
            "jaw_body_distance_m": float(fixture.np.linalg.norm(
                fixture.env.data.body("rightclaw").xpos-fixture.env.data.body("leftclaw").xpos)),
            "hand_z_m": float(fixture.env.get_endeff_pos()[2])})
    closure = aperture_evidence(samples[-5:])
    fixture.perform("observe", 1000)
    obs = fixture.latest
    negative = failed_pick_evidence(fixture.initial_blue,
        [s["target_pixel"] for s in obs["samples"]], [s["hand_pixel"] for s in obs["samples"]])
    gate = regrasp_gate(closure, negative,
        target_visible=obs["samples"][-1]["target_pixel"] is not None,
        observation_id=obs["id"], sole_grasp_candidate="blue_candidate",
        initial_empty_known=True, holding=obs["holding"])
    return {"closure": closure, "nonfollowing": negative, "gate": gate,
            "holding": obs["holding"], "observation_id": obs["id"]}


def regrasp(fixture, diagnosis):
    """Separate guarded macro; never reuse the PRE-CONTACT-only reset API."""
    if (not diagnosis["gate"]["authorized"] or fixture.latest["id"] != diagnosis["observation_id"]
            or fixture.latest["holding"].get("status") == "holding_supported"):
        raise ValueError("fresh_joint_gate_required")
    np = fixture.np
    origin = np.asarray(fixture.env.get_endeff_pos(), float)
    if not (abs(origin[0]) <= .3 and .4 <= origin[1] <= .95 and .12 <= origin[2] <= .35):
        return "outside_registered_workspace"
    # Consume the old observation BEFORE any world-changing command.
    fixture.latest = None
    fixture.phase = "joint_guarded_open_lift"
    target = origin.copy()
    target[2] = min(.30, max(.20, origin[2]+.06))
    fixture.grip = -1.
    reached = False
    for i in range(80):
        fixture.env.step(np.r_[np.clip(10*(target-fixture.env.get_endeff_pos()), -1, 1), -1.])
        reached = bool(np.linalg.norm(fixture.env.get_endeff_pos()-target) < .015)
        if reached and i >= 29:
            break
    if not reached:
        return "open_lift_timeout"
    rgb = fixture.env.render().copy()
    blue, green = fixture._pixel(rgb, "blue"), fixture._pixel(rgb, "green")
    # Registered target must still be where the preceding stationary-target
    # evidence put it. Opening is not a generic proof of an empty gripper.
    if (blue is None or green is None or np.linalg.norm(np.asarray(blue)-fixture.initial_blue) > 3):
        return "fresh_target_not_supported"
    obs_id = "after_joint_guarded_open_lift"
    fixture.phase = "joint_regrasp"
    receipt = fixture.controller.execute(fixture.contract.request(fixture._pick(),
        bindings=fixture._bindings(obs_id, blue, green), observation_id=obs_id))
    fixture.events.append({"action": "joint_regrasp", "receipt": receipt,
                           "binding_observation_id": obs_id})
    if not receipt["completed"]:
        return "regrasp_controller_incomplete"
    fixture.grip, fixture.precontact = 1., False
    fixture.perform("observe", 1000)
    # Waypoint completion must not recreate holding evidence.
    if "continue" not in fixture.allowed():
        return "fresh_holding_not_supported"
    fixture.perform("continue", 1000)
    return "regrasp_then_verified_continue"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source_dir = args.output/"sources"
    source_dir.mkdir()
    sources = [Path(__file__), Path("ch3/execution/recovery_contact_evidence.py"),
               Path("ch3/repair/budgeted_recovery_fixture.py"), Path("ch3/execution/fixed_pickplace.py"),
               Path("ch3/execution/visual_holding.py")]
    for source in sources:
        shutil.copyfile(source, source_dir/source.name)
    manifest = {"seeds": [1010, 1011, 1012], "cases": ["normal", "released", "precontact"],
        "purpose": "development feasibility of guarded joint-evidence retry; no algorithm comparison",
        "scope": "initially empty hand; only blue can be grasped; fixed plane; no new object",
        "dropout_disabled_for_every_case": True, "closure_steps": 30,
        "protocol_frozen_before_first_trial": True, "formal_seeds_960_979_unused": True,
        "source_hashes": {str(s): hashlib.sha256(s.read_bytes()).hexdigest() for s in sources}}
    (args.output/"manifest.json").write_text(json.dumps(manifest, indent=2))
    rows = []
    for seed in manifest["seeds"]:
        for case in manifest["cases"]:
            fixture = None
            row = {"seed": seed, "case": case, "status": "error", "success": False}
            try:
                fixture = RecoveryFixture(seed, case, args.output/f"seed_{seed}_{case}")
                fixture.blackout = 0
                before = fixture.steps
                diagnosis = diagnose(fixture)
                row["diagnosis"] = diagnosis
                if "continue" in fixture.allowed():
                    fixture.perform("continue", 1000)
                    route = "preserve_verified_grasp"
                elif diagnosis["gate"]["authorized"]:
                    route = regrasp(fixture, diagnosis)
                else:
                    route = "stop_insufficient_evidence"
                recovery_steps = fixture.steps-before
                # No policy/diagnosis callback after independent terminal score.
                success = fixture.score()
                row.update(status="complete", route=route, success=success,
                    recovery_steps=recovery_steps, prefix_steps=fixture.prefix_steps,
                    phase_steps=fixture.phase_steps, observations=fixture.observations,
                    events=fixture.events, episode_resets=int(fixture.env._ch3_reset_count), api_calls=0)
            except Exception as exc:
                row["error"] = {"type": type(exc).__name__, "message": str(exc)}
            finally:
                if fixture is not None:
                    fixture.close()
            rows.append(row)
            (args.output/"results.json").write_text(json.dumps(rows, indent=2))
            print(json.dumps({k: row.get(k) for k in ("seed", "case", "status", "route", "success", "recovery_steps", "error")}), flush=True)
    if any(r["status"] != "complete" for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
