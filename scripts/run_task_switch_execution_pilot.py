"""Finite pilot, NOT a formal benchmark. No automatic success-only reruns."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="preserve all existing records; mark interrupted launched cases rather than rerun them")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=args.resume)
    script = Path(__file__).with_name("task_switch_execution_smoke.py")
    cases = []
    for seed in range(3):
        settings = ["rule_recovery", "current_state_replan"] if seed % 2 == 0 else ["current_state_replan", "rule_recovery"]
        for setting in settings:
            cases.append({"case_id": f"seed{seed}_{setting}", "seed": seed, "setting": setting, "blackout_release": False})
    cases.append({"case_id": "seed0_release_blackout", "seed": 0, "setting": "rule_recovery", "blackout_release": True})
    manifest = {"protocol": "task_switch_execution_pilot_v1", "scope": "pilot_not_formal_statistics",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runner_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "cases": cases, "per_case_wall_timeout_s": 240,
        "model_decoding_seed": 0, "scene_seeds": [0, 1, 2], "episode_step_limit": 800,
        "no_success_only_reruns": True}
    manifest_path = args.output_dir / "manifest.json"
    if args.resume:
        existing = json.loads(manifest_path.read_text())
        if existing["cases"] != cases or existing["runner_sha256"] != manifest["runner_sha256"]:
            raise ValueError("cannot resume changed execution protocol")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2))
    record_path = args.output_dir / "records.jsonl"
    records = [json.loads(line) for line in record_path.read_text().splitlines()] if args.resume and record_path.exists() else []
    for i, case in enumerate(cases):
        if any(r["case_id"] == case["case_id"] for r in records):
            continue
        if args.resume and (args.output_dir / case["case_id"]).exists():
            record = dict(case, task_success=False, error="interrupted_previous_launch_not_rerun")
            summary_path = args.output_dir / case["case_id"] / "summary.json"
            if summary_path.exists():
                data = json.loads(summary_path.read_text())
                record.update({key: data.get(key) for key in ["task_success", "stage", "error", "model_calls", "yellow_pick_attempted", "control_steps"]})
            records.append(record)
            with record_path.open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            print(f"[switch-pilot] preserved interrupted case={case['case_id']}", flush=True)
            continue
        print(f"[switch-pilot] case={i+1}/{len(cases)} id={case['case_id']} start", flush=True)
        target = args.output_dir / case["case_id"]
        log = args.output_dir / (case["case_id"] + ".log")
        cmd = [sys.executable, str(script), "--env-file", args.env_file, "--seed", str(case["seed"]),
               "--setting", case["setting"], "--output-dir", str(target)]
        if case["blackout_release"]:
            cmd.append("--blackout-release")
        record = dict(case)
        try:
            with log.open("x") as stream:
                result = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
            record["exit_code"] = result.returncode
        except subprocess.TimeoutExpired:
            record["timeout"] = True
        summary = target / "summary.json"
        if summary.exists():
            data = json.loads(summary.read_text())
            record.update({key: data.get(key) for key in ["task_success", "stage", "error", "model_calls", "yellow_pick_attempted", "control_steps"]})
        else:
            record.update(task_success=False, error="missing_case_summary")
        records.append(record)
        with (args.output_dir / "records.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        print(f"[switch-pilot] case={i+1}/{len(cases)} result={json.dumps(record)}", flush=True)
    summary = {"completed_at_utc": datetime.now(timezone.utc).isoformat(), "completed_cases": len(records),
               "scope": "feasibility_pilot_not_formal_performance_claim", "records": records}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
