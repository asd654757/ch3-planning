"""Frozen 12-instruction semantic diagnostic; no new physical execution."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from ch3.vlm.client import DashScopeVLMClient
from ch3.vlm.task_semantics import TaskSemantics, parse_semantics, semantic_equal, deterministic_plan, evaluate_plan, generate_plan


def cases():
    def ready(goals, forbidden=(), order=()):
        return dict(status="ready", goals=[dict(object_id=o, target_id=t) for o,t in goals],
                    forbidden_objects=list(forbidden), placement_order=list(order), hand_empty=True)
    blue, yellow = "blue_candidate", "yellow_candidate"
    both = [(blue,"table"), (yellow,"green_region")]
    refusal = lambda status: dict(status=status, goals=[], forbidden_objects=[], placement_order=[], hand_empty=False)
    rows = [
        ("cancel_1", "cancellation", "Cancel the blue-to-green task. Put the blue cube back on the table and finish with an empty hand.", ready([(blue,"table")])),
        ("cancel_2", "cancellation", "蓝色方块不送去绿色区域了，把它放回桌面，然后结束。", ready([(blue,"table")])),
        ("switch_1", "object_change", "Return blue to the table; instead take yellow to the green region, then finish empty-handed.", ready(both)),
        ("switch_2", "object_change", "改搬黄色方块到绿色区域；手上的蓝块先放回桌面，最后空手。", ready(both)),
        ("reference_1", "held_reference", "Put the cube you are holding on the green region and finish empty-handed.", ready([(blue,"green_region")])),
        ("reference_2", "held_reference", "把手里那个放回桌面，不要碰黄色方块，然后结束。", ready([(blue,"table")], [yellow])),
        ("prohibition_1", "prohibition", "Place blue in the green region. Do not pick or place yellow. Finish with an empty hand.", ready([(blue,"green_region")], [yellow])),
        ("prohibition_2", "prohibition", "蓝块放回桌面，黄色方块禁止移动，最后空手。", ready([(blue,"table")], [yellow])),
        ("order_1", "ordering", "Put both cubes in green, blue first and yellow second, ending empty-handed.", ready([(blue,"green_region"),(yellow,"green_region")], order=[blue,yellow])),
        ("order_2", "ordering", "两个方块都放到绿色区域，必须先完成黄色的放置，再完成蓝色的放置，最后空手。可以临时将蓝色放回桌面。", ready([(blue,"green_region"),(yellow,"green_region")], order=[yellow,blue])),
        ("ambiguous_1", "refusal", "Put that cube over there.", refusal("clarify")),
        ("unavailable_1", "refusal", "Return blue to the table, then carry the orange cube to green.", refusal("unsupported")),
    ]
    return [dict(case_id=i, family=f, instruction=s, gold=g) for i,f,s,g in rows]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--env-file", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--parser-version", choices=["v2","v3","v4"], default="v4")
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=False)
    rows = cases()
    for row in rows:
        gold = TaskSemantics.model_validate(row["gold"])
        gold.check_scope()
        if gold.status == "ready" and deterministic_plan(gold) is None:
            raise ValueError("unreachable annotation: " + row["case_id"])
    root = Path(__file__).resolve().parents[1]
    manifest = dict(protocol="task_semantics_symbolic_pilot_"+a.parser_version, cases=rows,
        image=str(a.image), image_sha256=hashlib.sha256(a.image.read_bytes()).hexdigest(),
        commit=subprocess.check_output(["git","rev-parse","HEAD"], cwd=root, text=True).strip(),
        source_sha256={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
            ["scripts/task_semantics_pilot.py", "ch3/vlm/task_semantics.py"]},
        physical_execution=False, state_source="explicit_held_blue_fixture", model="qwen-vl-plus",
        automatic_reruns=False, parser_version=a.parser_version, semantic_image_input=a.parser_version=="v2", development_retest_of_v1=True, max_calls=24, annotation_in_model_prompt=False)
    (a.output_dir/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not a.execute:
        print(json.dumps(dict(manifest=str(a.output_dir/"manifest.json"), executed=False)))
        return
    client = DashScopeVLMClient(env_path=a.env_file, timeout=60, max_retries=0)
    records = []
    for row in rows:
        print(f"[semantics] case={row['case_id']} start", flush=True)
        target = a.output_dir/row["case_id"]
        target.mkdir()
        gold = TaskSemantics.model_validate(row["gold"])
        record = dict(case_id=row["case_id"], family=row["family"], semantic_correct=False,
                      end_to_end_symbolic_success=False, physical_execution=False)
        oracle = deterministic_plan(gold)
        record["oracle_semantics_symbolic_search"] = evaluate_plan(oracle,gold) if oracle is not None else {"decision":"refuse"}
        try:
            parsed = parse_semantics(client, instruction=row["instruction"], image_path=a.image,
                                     log_path=target/"semantic_call.json", version=a.parser_version)
            record["parsed_contract"] = parsed.model_dump()
            record["semantic_correct"] = semantic_equal(parsed,gold)
            record["refusal_correct"] = parsed.status == gold.status if gold.status != "ready" else None
            if parsed.status == "ready":
                search = deterministic_plan(parsed)
                record["parsed_semantics_symbolic_search"] = evaluate_plan(search,gold) if search is not None and gold.status == "ready" else {"accepted":False}
                raw = generate_plan(client, contract=parsed, image_path=a.image, log_path=target/"plan_call.json")
                record["plan"] = raw
                record["online_predicted_audit"] = evaluate_plan(raw,parsed)
                record["independent_gold_audit"] = evaluate_plan(raw,gold)
                record["end_to_end_symbolic_success"] = bool(record["semantic_correct"] and
                    record["online_predicted_audit"]["accepted"] and record["independent_gold_audit"]["accepted"])
                record["semantic_misaccept"] = bool(record["online_predicted_audit"]["accepted"] and
                    not record["independent_gold_audit"]["accepted"])
            else:
                record["end_to_end_symbolic_success"] = bool(record["semantic_correct"] and gold.status != "ready")
        except Exception as exc:
            record["error"] = str(exc)
            record["error_type"] = type(exc).__name__
        call_logs = [json.loads(f.read_text()) for f in target.glob("*_call.json")]
        record["logged_calls"] = len(call_logs)
        record["total_tokens"] = sum(c.get("total_tokens",0) or 0 for c in call_logs)
        records.append(record)
        with (a.output_dir/"records.jsonl").open("a") as f: f.write(json.dumps(record, ensure_ascii=False)+"\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
    summary = dict(completed_at_utc=datetime.now(timezone.utc).isoformat(), completed_cases=len(records),
        semantic_correct=sum(r["semantic_correct"] for r in records),
        end_to_end_symbolic_success=sum(r["end_to_end_symbolic_success"] for r in records),
        physical_execution=False, scope="restricted_candidates_explicit_state_semantic_diagnostic_not_formal")
    (a.output_dir/"summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)

if __name__ == "__main__": main()
