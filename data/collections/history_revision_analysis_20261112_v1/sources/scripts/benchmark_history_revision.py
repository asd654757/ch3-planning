"""Frozen bounded semantic-revision experiment; no physical execution.

All attempts are retained, without retries. Labels are passed only to scoring.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil

from ch3.revision.cases import json_default, make_cases, serialize_case
from ch3.revision.evaluation import mechanism_ablation, score
from ch3.revision.history import decide
from ch3.revision.model import METHODS, build_prompt, parse_output
from ch3.vlm.client import DashScopeVLMClient


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default)+"\n")


def summarize(rows):
    summary = {}
    for method in METHODS:
        selected = [r for r in rows if r["method"] == method]
        if not selected:
            continue
        metrics = ("semantic_exact", "status_correct", "valid_gold_suffix", "end_to_end", "unsafe_ready")
        summary[method] = {"n": len(selected), "errors": sum("error" in r for r in selected),
            **{k: sum(r.get("score", {}).get(k, False) for r in selected) for k in metrics},
            "tokens": sum(r.get("total_tokens", 0) for r in selected),
            "latency_ms_sum": sum(r.get("latency_ms", 0) for r in selected),
            "preserved": sum(r.get("score", {}).get("preserved", 0) for r in selected),
            "preservation_total_scored": sum(r.get("score", {}).get("preservation_total", 0) for r in selected)}
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--split", choices=("dev", "heldout"), default="dev")
    p.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    p.add_argument("--model", default="deepseek-v4.1-flash")
    p.add_argument("--env-path", type=Path, default=Path("/root/.config/ch3-planning/dashscope.env"))
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--oracle-only", action="store_true")
    args = p.parse_args()
    if not 1 <= args.workers <= 3 or len(set(args.methods)) != len(args.methods):
        p.error("workers must be 1..3 and methods unique")
    cases = make_cases(args.split)
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(Path.cwd().resolve()), *sorted(Path("ch3/revision").glob("*.py")),
               Path("ch3/vlm/client.py"), Path("ch3/vlm/task_semantics.py"),
               Path("docs/history_revision_protocol_20261112.md")]
    source_dir = args.output/"sources"
    source_dir.mkdir()
    hashes = {}
    for source in sources:
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        dest = source_dir/source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    write(args.output/"cases.json", [serialize_case(c) for c in cases])
    tasks = [(c, method) for c in cases for method in args.methods]
    random.Random(1211).shuffle(tasks)
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "split": args.split,
        "model": args.model, "methods": args.methods, "calls_planned": 0 if args.oracle_only else len(tasks),
        "temperature": .1, "seed": 0, "max_tokens": 4096, "max_retries": 0,
        "workers": args.workers, "horizon": 8, "source_hashes": hashes,
        "cases_sha256": hashlib.sha256((args.output/"cases.json").read_bytes()).hexdigest(),
        "call_order": [(c["id"], m) for c, m in tasks], "scope": "symbolic_author_built_12_families",
        "gold_in_prompt": False, "physical_execution": False}
    write(args.output/"manifest.json", manifest)
    write(args.output/"ablations.json", [mechanism_ablation(c) for c in cases])
    if args.oracle_only:
        print(json.dumps({"oracle_cases": len(cases), "status_correct": sum(
            decide(c["snapshot"], c["gold"]).status == c["gold_status"] for c in cases)}))
        return
    client = DashScopeVLMClient(model=args.model, env_path=args.env_path, timeout=60, max_retries=0)
    def trial(case, method):
        row = {"id": case["id"], "family": case["family"], "method": method,
               "gold_status": case["gold_status"]}
        system, user = build_prompt(case, method)
        row["system_prompt"], row["user_prompt"] = system, user
        try:
            response = client.complete(system_prompt=system, user_prompt=user,
                max_tokens=4096, seed=0, temperature=.1, enable_thinking=False)
            row.update({k: getattr(response, k) for k in ("content", "model", "latency_ms",
                "prompt_tokens", "completion_tokens", "total_tokens", "finish_reason", "request_id", "response_id")})
            proposed = parse_output(case, method, response.content)
            decision = decide(case["snapshot"], proposed)
            row.update(proposed=asdict(proposed), decision=asdict(decision), score=score(case, proposed, decision))
        except Exception as exc:
            # Keep failure type. Do not dump transport bodies or environment values.
            row["error"] = {"type": type(exc).__name__, "message": "request_or_semantic_validation_failed"}
            if "content" in row:
                row["error"]["message"] = str(exc)[:1500]
        return row
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(trial, c, method) for c, method in tasks]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            # A separate file per attempt survives interruption; all arms remain in denominator.
            write(args.output/f"{row['id']}__{row['method']}.json", row)
            write(args.output/"results.json", rows)
            print(json.dumps({"completed": len(rows), "id": row["id"], "method": row["method"],
                "exact": row.get("score", {}).get("semantic_exact"), "error": row.get("error", {}).get("type")}), flush=True)
    write(args.output/"summary.json", summarize(rows))
    write(args.output/"closeout.json", {"attempts": len(rows), "sources_unchanged": all(
        hashlib.sha256(Path(s).read_bytes()).hexdigest() == digest for s, digest in hashes.items())})
    print(json.dumps(summarize(rows)))


if __name__ == "__main__":
    main()
