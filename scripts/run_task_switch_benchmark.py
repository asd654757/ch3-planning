"""Frozen-manifest task-switch benchmark; not a general ROUTED benchmark.

All started cases, including timeouts/missing summaries, stay in the denominator.
No automatic reruns. Simulator truth is only read by child final evaluator.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def make_cases(seeds):
    cases = []
    variants = [("rule_with_observation", "rule_recovery", 2),
                ("replan_with_observation", "current_state_replan", 2),
                ("replan_without_extra_observation", "current_state_replan", 0)]
    for seed in range(seeds):
        ordered = variants[seed % 3:] + variants[:seed % 3]
        for name, setting, budget in ordered:
            cases.append(dict(case_id=f"seed{seed}_{name}", seed=seed, variant=name, setting=setting,
                              reobserve_budget=budget, blackout_destination=False, blackout_release=False))
        for name, setting in [("rule", "rule_recovery"), ("replan", "current_state_replan")]:
            cases.append(dict(case_id=f"seed{seed}_{name}_persistent_missing", seed=seed, variant=name + "_persistent_missing",
                              setting=setting, reobserve_budget=2, blackout_destination=True, blackout_release=False))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--execute", action="store_true", help="without this flag only freeze manifest")
    parser.add_argument("--phase", choices=["pilot", "formal"], default="pilot")
    args = parser.parse_args()
    if not 1 <= args.seeds <= 100:
        parser.error("seeds must be 1..100")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/task_switch_execution_smoke.py"
    tracked = subprocess.check_output(["git", "ls-files", "ch3", "scripts"], cwd=root, text=True).splitlines()
    code_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in tracked if (root / name).is_file()}
    cases = make_cases(args.seeds)
    manifest = {"protocol": "persistent_task_switch_observation_v1", "phase": args.phase,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "cases": cases,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "code_sha256": code_hashes, "scope": "controlled_language_update_known_color_fixture_not_open_world",
        "same_execution_backend_and_safety_gates": True, "episode_step_limit": 800,
        "wall_timeout_s": 240, "model": "qwen-vl-plus", "decoding_seed": 0,
        "analysis": "paired_by_scene_seed; observation_ablation_is_not_full_ROUTED_ablation",
        "persistent_missing_metric": "yellow_placement_not_attempted; not task completion",
        "failure_denominator": "all launched cases; infrastructure errors separately labelled", "automatic_reruns": False}
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if not args.execute:
        print(json.dumps({"manifest": str(args.output_dir / "manifest.json"), "cases": len(cases), "executed": False}))
        return
    records = []
    for index, case in enumerate(cases):
        print(f"[switch-benchmark] case={index+1}/{len(cases)} id={case['case_id']} start", flush=True)
        target = args.output_dir / case["case_id"]
        command = [sys.executable, str(script), "--env-file", args.env_file, "--seed", str(case["seed"]), "--setting", case["setting"],
                   "--reobserve-budget", str(case["reobserve_budget"]), "--output-dir", str(target)]
        if case["blackout_destination"]:
            command.append("--blackout-destination")
        record = dict(case, task_success=False)
        try:
            with (args.output_dir / (case["case_id"] + ".log")).open("x") as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=240, cwd=root)
            record["exit_code"] = result.returncode
        except subprocess.TimeoutExpired:
            record["infrastructure_error"] = "wall_timeout"
        summary = target / "summary.json"
        if summary.exists():
            data = json.loads(summary.read_text())
            record.update({key: data.get(key) for key in ["task_success", "stage", "error", "model_calls", "control_steps", "reobservations", "episode_resets", "recovery_episode_resets"]})
            record["yellow_placement_attempted"] = any(e["stage"] == "yellow_place" for e in data.get("events", []))
            if case["blackout_destination"]:
                record["safe_stop"] = not record["yellow_placement_attempted"]
            calls = [json.loads(p.read_text()) for p in target.glob("*_call.json")]
            record["total_tokens"] = sum(c.get("total_tokens", 0) or 0 for c in calls)
        else:
            record.setdefault("infrastructure_error", "missing_summary")
        records.append(record)
        with (args.output_dir / "records.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        print(f"[switch-benchmark] case={index+1}/{len(cases)} result={json.dumps(record)}", flush=True)
    summary = {"completed_at_utc": datetime.now(timezone.utc).isoformat(), "completed_cases": len(records), "phase": args.phase,
               "protocol": manifest["protocol"], "records": records}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"completed_cases": len(records), "summary": str(args.output_dir / "summary.json")}), flush=True)


if __name__ == "__main__":
    main()
