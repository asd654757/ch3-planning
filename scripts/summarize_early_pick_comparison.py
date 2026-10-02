"""Audit bounded comparison; image equality is not formal statistical evidence."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(sources):
    rows = []
    for directory in sources:
        summary = json.loads((directory / "summary.json").read_text())
        recovery = summary.get("early_recovery", {})
        initial_pick = summary.get("initial_pick_result", recovery.get("initial_pick_result"))
        validation = directory / "initial_plan_validation.json"
        plan = json.loads(validation.read_text()).get("plan") if validation.exists() else None
        hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                  for name in ["before.png", "after_pick.png", "early_recovery.png"] if (directory / name).exists()}
        calls = [json.loads(p.read_text()) for p in directory.glob("*_call.json")]
        rows.append({"source": str(directory), "setting": summary.get("recovery_setting"),
                     "stage": summary.get("stage"), "error": summary.get("error"),
                     "task_success": summary.get("task_success", False),
                     "model_calls": summary.get("model_calls"),
                     "logged_total_tokens": sum(c.get("total_tokens", 0) or 0 for c in calls),
                     "pick_attempts": summary.get("pick_attempts"), "initial_pick_result": initial_pick,
                     "initial_plan": plan, "image_hashes": hashes,
                     "reset": recovery.get("reset"), "retry_pick": recovery.get("retry_pick_result"),
                     "place_execution": summary.get("place_execution"),
                     "probe_steps": summary.get("probe_steps", 0)})
    def same(field):
        values = [row[field] for row in rows]
        return bool(values) and all(v is not None and v == values[0] for v in values)
    matching = {name: len(rows) > 1 and all(name in r["image_hashes"] for r in rows)
                and len({r["image_hashes"][name] for r in rows}) == 1 for name in ["before.png", "after_pick.png"]}
    return {"scope": "single_seed_live_calls_diagnostic_not_formal_performance_comparison",
            "same_initial_plan": same("initial_plan"), "same_initial_timeout_trace": same("initial_pick_result"),
            "matching_images": matching, "records": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = summarize(args.sources)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
