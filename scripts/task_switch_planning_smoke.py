"""One live task-switch planning call; no physical execution or state inference."""
import argparse
import json
from pathlib import Path

from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.task_switch_recovery import evaluate_switch_plan, generate_switch_plan, switch_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    state = switch_fixture()
    # These are labelled hand-written diagnostic controls, not live external baselines.
    old = {"actions": [dict(step_id=1, skill="place", arm="right", object_id="blue_candidate", target_id="green_region")]}
    rule = {"actions": [dict(step_id=1, skill="place", arm="right", object_id="blue_candidate", target_id="table"),
        dict(step_id=2, skill="pick", arm="right", object_id="yellow_candidate"),
        dict(step_id=3, skill="place", arm="right", object_id="yellow_candidate", target_id="green_region")]}
    summary = {"scope": "explicit_state_symbolic_diagnostic_not_online_simulation",
               "image_source": str(args.image), "task_update_source": "scripted_external_command_not_model_failure",
               "historical_remainder": evaluate_switch_plan(old, state)[0],
               "handwritten_release_then_transport_control": evaluate_switch_plan(rule, state)[0],
               "physical_execution_attempted": False}
    try:
        executable = generate_switch_plan(DashScopeVLMClient(env_path=args.env_file, timeout=60, max_retries=0),
            image_path=args.image, log_path=args.output_dir / "switch_plan_call.json", state=state)
        summary.update(model_plan_accepted=True, executable_plan=executable.to_list())
    except Exception as exc:
        summary.update(model_plan_accepted=False, error=str(exc))
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
