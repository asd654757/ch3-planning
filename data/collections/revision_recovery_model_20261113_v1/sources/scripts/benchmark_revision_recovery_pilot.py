"""Frozen development pilot: model translation/decision -> guarded symbolic dispatch.

This is NOT a robot benchmark or a held-out innovation demonstration. Every
attempt, including invalid proposals and transport errors, remains archived.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
from time import perf_counter

from ch3.revision.cases import json_default
from ch3.revision.evaluation import score
from ch3.revision.history import decide, monitor, possible_histories, successors
from ch3.revision.recovery_pilot import METHODS, HORIZON, candidate, make_pilot_cases, prompt
from ch3.revision.runtime import RevisionRuntime
from ch3.vlm.client import DashScopeVLMClient


def encode(value):
    return asdict(value) if is_dataclass(value) else json_default(value)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode)+"\n")


def symbolic_dispatch(case, proposed, decision):
    """Controlled deterministic observations, not RGB evidence or physical success.

    The submitted suffix is only validated here; no automatic solver repairs.
    A real backend must replace successors with observed controller outcomes.
    """
    runtime = RevisionRuntime(case["snapshot"], HORIZON)
    runtime.accept(proposed, expected_digest=runtime.snapshot.digest, expected_generation=runtime.generation,
                   supplied_decision=decision)
    before = runtime.snapshot.history
    trace = []
    if decision.status != "ready":
        return {"dispatched_actions": 0, "trace": trace, "ledger_unchanged": runtime.snapshot.history == before,
                "task_complete": False, "physical_execution": False}
    remaining = decision.plan
    while remaining:
        ticket = runtime.issue()
        command = runtime.consume(ticket)
        assert runtime.snapshot.history == (*before, *(step["observed_event"] for step in trace))
        state, event = next((ns, e) for ns, e in successors(runtime.snapshot.state, runtime.snapshot.cursor+1)
                           if (e.kind, e.object, e.target) == (remaining[0].kind, remaining[0].object, remaining[0].target))
        obs = f"controlled-symbolic-observation-{event.seq}"
        event = replace(event, observation_id=obs)
        runtime.observe(state, observation_id=obs, events=(event,))
        trace.append({"command": command.model_dump(mode="json"), "observed_event": event, "observed_state": state})
        remaining = remaining[1:]
        runtime.assess(remaining, allow_replan=False)
    # Evaluation only, AFTER all dispatch. Gold never changes a command.
    completed = all(monitor(case["gold"], runtime.snapshot.state, h) == (frozenset(), frozenset())
                    for h in possible_histories(runtime.snapshot.history))
    return {"dispatched_actions": len(trace), "trace": trace, "initial_ledger_preserved": runtime.snapshot.history[:len(before)] == before,
            "task_complete": completed, "physical_execution": False}


def run_trial(client, case, method, checkpoint=None):
    row = {"id": case["id"], "family": case["family"], "experiment": case["experiment"], "method": method,
           "gold_status": case["gold_status"], "attempts": [], "accepted": False, "tokens": 0}
    feedback = None
    for index in range(2):
        system, user = prompt(case, method, feedback)
        attempt = {"index": index+1, "system_prompt": system, "user_prompt": user}
        row["attempts"].append(attempt)
        try:
            response = client.complete(system_prompt=system, user_prompt=user,
                max_tokens=4096, temperature=.1, seed=0, enable_thinking=False)
            attempt.update({k: getattr(response, k) for k in ("content", "model", "latency_ms", "prompt_tokens",
                "completion_tokens", "total_tokens", "finish_reason", "request_id", "response_id")})
            row["tokens"] += response.total_tokens
            started = perf_counter()
            proposed, decision, route, error = candidate(case, method, response.content)
            attempt.update(program_ms=(perf_counter()-started)*1000, proposed=proposed, decision=decision,
                           route=route, score=score(case, proposed, decision), rejection=error)
            if error is None:
                dispatch = symbolic_dispatch(case, proposed, decision)
                row.update(accepted=True, score=attempt["score"], route=route,
                           dispatch=dispatch)
                break
            feedback = {"error_code": error, "rejected_candidate_not_executed": response.content,
                        "instruction": "Regenerate the complete output for your assigned schema. Original task/history remain unchanged."}
        except Exception as exc:
            # Never include exception transport bodies/config values in archives.
            attempt["error"] = {"type": type(exc).__name__, "message": "request_parse_or_dispatch_failed"}
            feedback = {"error_code": type(exc).__name__, "instruction": "Return valid JSON matching the assigned schema."}
            if "content" in attempt:
                attempt["error"]["message"] = str(exc)[:1200]
                feedback["rejected_candidate_not_executed"] = attempt["content"]
        finally:
            if checkpoint:
                checkpoint(row)
    row["calls"] = len(row["attempts"])
    row["first_pass"] = bool(row["attempts"][0].get("rejection") is None and
                             "score" in row["attempts"][0] and "error" not in row["attempts"][0])
    return row


def summarize(rows):
    result = {}
    for method in METHODS:
        chosen = [r for r in rows if r["method"] == method]
        ready = [r for r in chosen if r["gold_status"] == "ready"]
        result[method] = {"n": len(chosen), "accepted": sum(r["accepted"] for r in chosen),
            "end_to_end": sum(r.get("score", {}).get("end_to_end", False) and r["accepted"] for r in chosen),
            "semantic_exact": sum(r.get("score", {}).get("semantic_exact", False) for r in chosen),
            "status_correct": sum(r.get("score", {}).get("status_correct", False) for r in chosen),
            "first_pass_accepted": sum(r["first_pass"] for r in chosen),
            "feasible_cases": len(ready), "symbolic_task_complete": sum(r.get("dispatch", {}).get("task_complete", False) for r in ready),
            "nonready_cases": len(chosen)-len(ready),
            "calls": sum(r["calls"] for r in chosen), "tokens": sum(r["tokens"] for r in chosen),
            "rejected_candidates": sum(bool(a.get("rejection")) for r in chosen for a in r["attempts"]),
            "errors": sum("error" in a for r in chosen for a in r["attempts"]),
            "unsafe_ready_proposals": sum(a.get("score", {}).get("unsafe_ready", False) for r in chosen for a in r["attempts"]),
            "false_stops": sum(r["accepted"] and r["gold_status"] == "ready" and
                               r["attempts"][-1]["decision"].status != "ready" for r in chosen),
            "dispatched_actions": sum(r.get("dispatch", {}).get("dispatched_actions", 0) for r in chosen),
            "reuse_cases": sum(r.get("route") == "REUSE_VALID_SUFFIX" for r in chosen),
            "program_ms": sum(a.get("program_ms", 0) for r in chosen for a in r["attempts"]),
            "latency_ms": sum(a.get("latency_ms", 0) for r in chosen for a in r["attempts"])}
        result[method]["by_experiment"] = {e: {"n": sum(r["experiment"] == e for r in chosen),
            "end_to_end": sum(r["experiment"] == e and r.get("score", {}).get("end_to_end", False) and r["accepted"] for r in chosen)} for e in ("E1", "E2")}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2))
    parser.add_argument("--oracle-only", action="store_true")
    args = parser.parse_args()
    cases = make_pilot_cases()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve().relative_to(Path.cwd().resolve()),
        *sorted(Path("ch3/revision").glob("*.py")), Path("ch3/vlm/client.py"), Path("ch3/vlm/task_semantics.py"),
        Path("docs/revision_recovery_development_protocol_20261113.md"), Path("tests/test_revision_recovery_pilot.py")]
    hashes = {}
    for source in sources:
        hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = args.output/"sources"/source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    tasks = [(c, m) for c in cases for m in METHODS]
    random.Random(1311).shuffle(tasks)
    write(args.output/"cases.json", cases)
    write(args.output/"manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "development_only_4_recovery_8_reused_semantic_cases", "physical_execution": False,
        "model": args.model, "methods": METHODS, "case_count": len(cases), "max_calls": 72,
        "max_tokens_per_call": 4096, "max_calls_per_case_method": 2, "horizon": HORIZON,
        "temperature": .1, "seed": 0, "transport_retries": 0, "workers": args.workers,
        "source_hashes": hashes, "cases_sha256": hashlib.sha256((args.output/"cases.json").read_bytes()).hexdigest(),
        "order": [(c["id"], m) for c, m in tasks], "gold_in_prompt": False,
        "solver_shared_with_full_baseline": True, "online_extra_observations": 0})
    if args.oracle_only:
        rows = []
        for c in cases:
            decision = decide(c["snapshot"], c["gold"], HORIZON)
            rows.append({"id": c["id"], "status_matches": decision.status == c["gold_status"],
                "dispatch": symbolic_dispatch(c, c["gold"], decision)})
        write(args.output/"oracle_audit.json", rows)
        print(json.dumps({"cases": len(rows), "status_matches": sum(r["status_matches"] for r in rows),
            "feasible_completions": sum(r["dispatch"]["task_complete"] for r in rows), "model_calls": 0}))
        return
    client = DashScopeVLMClient(model=args.model, env_path="/root/.config/ch3-planning/dashscope.env", timeout=60, max_retries=0)
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_trial, client, c, m,
                    lambda row, c=c, m=m: write(args.output/f"{c['id']}__{m}.json", row)) for c, m in tasks]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            write(args.output/f"{row['id']}__{row['method']}.json", row)
            write(args.output/"results.json", rows)
            print(json.dumps({"completed": len(rows), "id": row["id"], "method": row["method"],
                "calls": row["calls"], "end_to_end": row.get("score", {}).get("end_to_end", False)}), flush=True)
    write(args.output/"summary.json", summarize(rows))
    write(args.output/"closeout.json", {"case_method_pairs": len(rows), "calls": sum(r["calls"] for r in rows),
        "sources_unchanged": all(hashlib.sha256(Path(s).read_bytes()).hexdigest() == v for s, v in hashes.items())})
    print(json.dumps(summarize(rows)))


if __name__ == "__main__":
    main()
