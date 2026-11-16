"""Frozen five-arm development comparison; no physical robot execution."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
from time import perf_counter

from ch3.context_memory.cases import load_cases, make_cases
from ch3.context_memory.core import (METHODS, build_memory, dependency_ids, digest,
    evaluate, prompt, select, symbolic_replay, verify_memory_sources)
from ch3.revision.evaluation import valid_suffix
from ch3.revision.history import decide, monitor, possible_histories
from ch3.vlm.client import DashScopeVLMClient
from scripts.benchmark_revision_recovery_pilot import encode, write


def audit_case(case, memory):
    s = case["snapshot"]
    decision = decide(s, s.contract)
    if decision.status != case["expected_status"]:
        raise ValueError("preassigned label differs from bounded reference")
    keep = dependency_ids(s)
    for history in possible_histories(s.history):
        filtered = tuple(e for e in history if "event:"+str(e.seq) in keep)
        for suffix in ((), decision.plan):
            if monitor(s.contract, s.state, (*history, *suffix)) != monitor(s.contract, s.state, (*filtered, *suffix)):
                raise ValueError("dependency filtering changed trace semantics")
    inputs = {m: prompt(case, m, memory) for m in METHODS}
    base = json.loads(inputs["full"][1])
    for method, (system, user, _) in inputs.items():
        payload = json.loads(user)
        if system != inputs["full"][0] or any(payload[k] != v for k, v in base.items()
            if k not in ("OBSERVED_CONTEXT", "EXPERIENCES")):
            raise ValueError("nonselection input differs between arms")
        changed = dict(case, id="SECRET", family="SECRET", expected_status="SECRET", split="SECRET", background_events=-1)
        if prompt(changed, method, memory) != inputs[method]:
            raise ValueError("evaluation annotation reached prompt or selector")
    return {"id": case["id"], "expected_status": case["expected_status"], "label_matches": True,
        "filtered_monitor_equivalent": True, "inputs_match_except_selection": True,
        "joint_selection": inputs["joint"][2], "physical_execution": False}


def run_trial(client, case, method, memory, checkpoint=None):
    row = {"id": case["id"], "family": case["family"], "method": method,
        "expected_status": case["expected_status"], "accepted": False, "attempts": [], "tokens": 0, "calls": 0}
    feedback = None
    for i in range(2):
        started = perf_counter()
        system, user, selection = prompt(case, method, memory, feedback)
        item = {"index": i+1, "system_prompt": system, "user_prompt": user, "selection": selection,
            "selection_ms": (perf_counter()-started)*1000, "input_utf8_bytes": len((system+user).encode())}
        row["attempts"].append(item)
        try:
            row["calls"] += 1
            response = client.complete(system_prompt=system, user_prompt=user,
                max_tokens=2048, temperature=.1, seed=0, enable_thinking=False)
            item.update({k: getattr(response, k) for k in ("content", "model", "latency_ms", "prompt_tokens",
                "completion_tokens", "total_tokens", "finish_reason", "request_id", "response_id")})
            row["tokens"] += response.total_tokens
            started = perf_counter()
            decision, rejection = evaluate(case["snapshot"], response.content)
            item.update(decision=asdict(decision), rejection=rejection, validation_ms=(perf_counter()-started)*1000,
                status_correct=decision.status == case["expected_status"],
                unsafe_ready=decision.status == "ready" and not valid_suffix(case["snapshot"], case["snapshot"].contract, decision.plan))
            if rejection is None:
                dispatch = symbolic_replay(case["snapshot"], decision)
                row.update(accepted=True, correct=item["status_correct"], dispatch=dispatch,
                    final_status=decision.status)
                break
            feedback = {**rejection, "rejected_candidate_not_executed": response.content}
        except Exception as exc:
            item["error"] = {"type": type(exc).__name__, "message": "model_parse_or_dispatch_failed"}
            feedback = {"error_code": type(exc).__name__, "instruction": "Return valid JSON matching the same schema and full task."}
            if "content" in item:
                item["error"]["message"] = str(exc)[:1000]
                feedback["rejected_candidate_not_executed"] = item["content"]
        finally:
            if checkpoint:
                checkpoint(row)
    first = row["attempts"][0]
    row["first_correct"] = bool(first.get("status_correct") and first.get("rejection") is None and "error" not in first)
    row.setdefault("correct", False)
    return row


def summarize(rows):
    result = {}
    for method in METHODS:
        chosen = [r for r in rows if r["method"] == method]
        feasible = [r for r in chosen if r["expected_status"] == "ready"]
        result[method] = {"n": len(chosen), "first_correct": sum(r["first_correct"] for r in chosen),
            "final_correct": sum(r["correct"] and r["accepted"] for r in chosen),
            "feasible_cases": len(feasible), "symbolic_completions": sum(r.get("dispatch", {}).get("task_complete", False) for r in feasible),
            "nonready_cases": len(chosen)-len(feasible), "calls": sum(r["calls"] for r in chosen),
            "tokens": sum(r["tokens"] for r in chosen),
            "prompt_tokens": sum(a.get("prompt_tokens", 0) for r in chosen for a in r["attempts"]),
            "completion_tokens": sum(a.get("completion_tokens", 0) for r in chosen for a in r["attempts"]),
            "rejected_candidates": sum(a.get("rejection") is not None for r in chosen for a in r["attempts"] if "decision" in a),
            "errors": sum("error" in a for r in chosen for a in r["attempts"]),
            "unsafe_ready_proposals": sum(a.get("unsafe_ready", False) for r in chosen for a in r["attempts"]),
            "erroneous_nonready_proposals": sum(r["expected_status"] == "ready" and a["decision"]["status"] != "ready"
                for r in chosen for a in r["attempts"] if "decision" in a),
            "dispatched_actions": sum(r.get("dispatch", {}).get("dispatched_actions", 0) for r in chosen),
            "selected_context_records_sum": sum(r["attempts"][0]["selection"]["selected_records"] for r in chosen),
            "selected_memories_sum": sum(len(r["attempts"][0]["selection"]["selected_memory_ids"]) for r in chosen),
            "joint_added_context_cases": sum(bool(r["attempts"][0]["selection"]["extra_records_from_memory"]) for r in chosen),
            "selector_ms_sum": sum(a["selection_ms"] for r in chosen for a in r["attempts"]),
            "api_latency_ms_sum": sum(a.get("latency_ms", 0) for r in chosen for a in r["attempts"])}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "validation"), default="development")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--frozen-input", type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    memory = build_memory(root)
    memory_audit = verify_memory_sources(root, memory)
    if args.frozen_input:
        prior = json.loads((args.frozen_input/"manifest.json").read_text())
        if (digest(args.frozen_input/"cases.json") != prior["cases_sha256"] or
            digest(args.frozen_input/"memory.json") != prior["memory_sha256"]):
            raise ValueError("frozen input changed")
        if json.loads(json.dumps(memory)) != json.loads((args.frozen_input/"memory.json").read_text()):
            raise ValueError("experience content changed after input freeze")
        cases = load_cases(args.frozen_input/"cases.json")
        if any(c["split"] != args.split for c in cases):
            raise ValueError("frozen case split mismatch")
    else:
        cases = make_cases(args.split)
    audits = [audit_case(c, memory) for c in cases]
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(root), *sorted(Path("ch3/context_memory").glob("*.py")),
        *sorted(Path("ch3/revision").glob("*.py")), Path("ch3/vlm/client.py"), Path("ch3/vlm/task_semantics.py"),
        Path("ch3/schema/model_plan.py"), Path("scripts/benchmark_revision_recovery_pilot.py"),
        Path("scripts/analyze_history_revision.py"), Path("tests/test_context_memory.py"),
        Path("docs/context_memory_development_protocol_20261113.md")]
    hashes = {}
    for source in sources:
        hashes[str(source)] = digest(source)
        target = args.output/"sources"/source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    write(args.output/"memory.json", memory)
    write(args.output/"memory_source_audit.json", memory_audit)
    write(args.output/"cases.json", cases)
    write(args.output/"oracle_audit.json", audits)
    tasks = [(c, method) for c in cases for method in METHODS]
    random.Random(1321).shuffle(tasks)
    write(args.output/"frozen_initial_prompts.json", [{"id": c["id"], "method": m,
        "system": prompt(c, m, memory)[0], "user": prompt(c, m, memory)[1]} for c, m in tasks])
    write(args.output/"manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(), "user_timezone": "Asia/Shanghai",
        "split": args.split, "model": args.model, "methods": METHODS, "instance_method_pairs": len(tasks),
        "max_api_calls": 0 if args.prepare_only else len(tasks)*2, "max_calls_per_pair": 2,
        "max_tokens_per_call": 2048, "temperature": .1, "seed": 0, "enable_thinking": False,
        "transport_retries": 0, "workers": 2, "source_hashes": hashes,
        "cases_sha256": digest(args.output/"cases.json"), "memory_sha256": digest(args.output/"memory.json"),
        "initial_prompts_sha256": digest(args.output/"frozen_initial_prompts.json"),
        "order": [(c["id"], m) for c, m in tasks], "physical_execution": False,
        "experience_entries": 2, "memory_abstractions": "human_authored", "gold_in_prompt": False,
        "frozen_input": str(args.frozen_input) if args.frozen_input else None,
        "memory_gate_version": "eligible_order_evidence_confirmed_or_unknown",
        "method_boundary": "joint_equals_strong_dependency_filter_plus_applicability_gating_in_these_cases"})
    if args.prepare_only:
        print(json.dumps({"prepared_cases": len(cases), "label_matches": len(audits), "memory_sources_replayed": len(memory_audit),
            "joint_adds_context_cases": sum(bool(a["joint_selection"]["extra_records_from_memory"]) for a in audits), "api_calls": 0}))
        return
    client = DashScopeVLMClient(model=args.model, env_path="/root/.config/ch3-planning/dashscope.env", timeout=60, max_retries=0)
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_trial, client, c, m, memory,
            lambda row, c=c, m=m: write(args.output/f"{c['id']}__{m}.json", row)) for c, m in tasks]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            write(args.output/f"{row['id']}__{row['method']}.json", row)
            write(args.output/"results.json", rows)
            print(json.dumps({"completed": len(rows), "id": row["id"], "method": row["method"],
                "calls": row["calls"], "first_correct": row["first_correct"], "final_correct": row["correct"]}), flush=True)
    write(args.output/"summary.json", summarize(rows))
    write(args.output/"closeout.json", {"pairs": len(rows), "calls": sum(r["calls"] for r in rows),
        "tokens": sum(r["tokens"] for r in rows), "sources_unchanged": all(digest(p) == h for p, h in hashes.items()),
        "cases_unchanged": digest(args.output/"cases.json") == json.loads((args.output/"manifest.json").read_text())["cases_sha256"],
        "memory_unchanged": digest(args.output/"memory.json") == json.loads((args.output/"manifest.json").read_text())["memory_sha256"]})
    print(json.dumps(summarize(rows)))


if __name__ == "__main__":
    main()
