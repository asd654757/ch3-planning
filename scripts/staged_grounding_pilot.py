"""Two-stage front-end smoke; physical execution intentionally unavailable."""

import argparse
import hashlib
import json
from pathlib import Path

from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.scene_grounding import GroundingParseError
from ch3.vlm.staged_grounding import StagedGrounder
from scripts.unstructured_grounding_pilot import load_manifest


def provenance(response):
    return {"raw_response": response.content, "model": response.model,
            "total_tokens": response.total_tokens, "latency_ms": response.latency_ms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--env-file")
    parser.add_argument("--model", default="qwen3-vl-flash")
    args = parser.parse_args()
    rows = load_manifest(Path(args.manifest))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        grounder = StagedGrounder(DashScopeVLMClient(model=args.model, env_path=args.env_file))
        for row in rows:
            record = {**row, "record_type": "staged_grounding_case", "stages": {},
                      "image_sha256": hashlib.sha256(Path(row["image_path"]).read_bytes()).hexdigest(),
                      "accepted_for_planning": False, "execution_attempted": False}
            phase = "localization"
            print(f"[staged-grounding] case={row['case_id']} start", flush=True)
            try:
                located, response = grounder.localize(image_path=row["image_path"], seed=row["seed"])
                record["stages"][phase] = provenance(response)
                record["localization"] = located.model_dump(mode="json")
                phase = "semantics"
                scene, response = grounder.interpret(
                    localization=located, instruction=row["instruction"], image_path=row["image_path"],
                    arms=["right"], skills=["pick", "place", "push", "press"], seed=row["seed"],
                )
                record["stages"][phase] = provenance(response)
                record["grounding"] = scene.model_dump(mode="json")
                state, goal = scene.to_planning_input(arms={"right"})
                record.update({"accepted_for_planning": True, "parsed_goal": goal.facts,
                               "estimated_facts": sorted(state.facts() | state.empty_hand_facts({"right"}))})
            except GroundingParseError as exc:
                record["stages"][phase] = provenance(exc.response)
                record["error"] = str(exc)
            except Exception as exc:
                record["error"] = str(exc)
            record["failed_stage"] = phase if not record["accepted_for_planning"] else None
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            print(json.dumps({"case_id": row["case_id"], "accepted_for_planning": record["accepted_for_planning"],
                              "failed_stage": record["failed_stage"]}), flush=True)


if __name__ == "__main__":
    main()
