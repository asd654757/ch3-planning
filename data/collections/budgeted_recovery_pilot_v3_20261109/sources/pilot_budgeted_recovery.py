"""Calibrate on development rollouts, tune a rule, then run a separate pilot.

No LLM baseline is reproduced. Sensor outages are controlled acquisition-index
faults, not measured natural fault persistence. This is a development experiment.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
from math import ceil
import os
from pathlib import Path
import time

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.repair.budgeted_recovery_fixture import CASES, RecoveryFixture, ResourceLimit
from ch3.repair.recovery_decision import ActionModel, Branch, Budget, Context, RecoveryDecisionPlanner, RecoveryModel

DEVELOPMENT_SEEDS = (930, 931, 932)
PILOT_SEEDS = (940, 941)
FAMILIES = ("post_pick", "precontact", "released")
MAX_OBSERVATIONS = 3


def unknown(family, count):
    return f"{family}/unknown/{count}"


def fixed_rule(ctx, allowed, threshold, minima=None):
    for action in ("continue", "retry"):
        if action in allowed:
            return action
    state = next(iter(ctx.belief))
    if "/unknown/" not in state:
        return "stop"  # Known-ready but resources/guard do not allow execution.
    if minima and state in minima and ctx.budget.spend(minima[state]) is None:
        return "stop"
    count = int(state.rsplit("/",1)[-1])
    return "observe" if count < threshold and "observe" in allowed else "stop"


def run_episode(seed, case, output, *, model=None, caps=None, budget=None, method="calibrate", rule_limit=3):
    row = {"seed": seed, "case": case, "method": method, "status": "error", "decisions": [],
           "api_calls": 0, "task_success": False, "model_used": "none" if model is None else model.provenance}
    fixture = None
    clock = time.perf_counter()
    try:
        fixture = RecoveryFixture(seed, case, output)
        row["public_family"] = fixture.family
        spent = 0
        observations = 0
        remaining = Budget(4, 2, 0, 10000) if budget is None else budget
        row["initial_budget"] = asdict(remaining)
        state = unknown(fixture.family, 0)
        planner = RecoveryDecisionPlanner(model, depth=1 if method == "depth1" else 2) if model else None
        for i in range(6):
            enabled = fixture.allowed()
            if observations >= MAX_OBSERVATIONS:
                enabled -= {"observe"}
            costs = ({"observe": Budget(1,0,0,1000), "continue": Budget(0,1,0,1000),
                      "retry": Budget(1,2,0,1000)} if caps is None else caps)
            allowed = {a for a in enabled if a == "stop" or remaining.spend(costs[a]) is not None}
            ctx = Context(f"actual:{i}", {state: 1.}, remaining, frozenset(allowed))
            if method in {"calibrate", "rule"}:
                action = fixed_rule(ctx, allowed, rule_limit, model.leaf_min_resources if model else None)
                values = None
            else:
                decision = planner.choose(ctx)
                action, values = decision.action, decision.values
            event = {"index": i, "state": state, "observation_id": ctx.observation_id,
                     "allowed": sorted(allowed), "action": action, "values": values,
                     "budget_before": asdict(remaining)}
            row["decisions"].append(event)
            if action == "stop":
                break
            cap = costs[action]
            label, steps = fixture.perform(action, cap.time_units)
            spent += steps
            # Actual consumed resources update all policies identically.
            actual = Budget(cap.observations, cap.commands, cap.model_calls, steps)
            remaining = remaining.spend(actual)
            if remaining is None:
                raise RuntimeError("resource_accounting_violation")
            event.update(label=label, actual_cost=asdict(actual))
            if action != "observe":
                break
            observations += 1
            state = label if label != "unknown" else unknown(fixture.family, observations)
        row.update(status="complete", recovery_control_steps=spent,
                   remaining_budget=asdict(remaining))
    except Exception as exc:
        row["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, ResourceLimit):
            row["status"] = "macro_cap_exceeded"
    finally:
        if fixture:
            # Independent terminal scoring only after policy loop has ended.
            try:
                row["task_success"] = fixture.score()
            except Exception as exc:
                row["scoring_error"] = type(exc).__name__ + ": " + str(exc)
                row["status"] = "error"
            row.update(events=fixture.events, observations=fixture.observations,
                       episode_resets=int(fixture.env._ch3_reset_count), phase_steps=fixture.phase_steps,
                       recovery_control_steps=fixture.steps-fixture.prefix_steps-fixture.phase_steps.get("terminal_scoring",0))
            fixture.close()
        row["wall_seconds"] = time.perf_counter()-clock
        output.mkdir(parents=True, exist_ok=True)
        (output/"result.json").write_text(json.dumps(row, indent=2))
    return row


def fit_model(rows, provenance):
    """Empirical history-conditioned hazard; no latent case label at inference."""
    if not rows or any(r["status"] != "complete" for r in rows):
        raise ValueError("complete failure-inclusive calibration records required")
    counts = defaultdict(lambda: defaultdict(int))
    duration_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    outcomes = defaultdict(lambda: [0, 0])
    leaf = defaultdict(lambda: [0, 0])
    max_steps = defaultdict(int)
    for row in rows:
        for event in row["decisions"]:
            state, action = event["state"], event["action"]
            leaf[state][0] += int(row["task_success"])
            leaf[state][1] += 1
            if "actual_cost" not in event:
                continue
            max_steps[action] = max(max_steps[action], event["actual_cost"]["time_units"])
            if action == "observe":
                counts[state][event["label"]] += 1
                duration_counts[state][event["label"]][event["actual_cost"]["time_units"]] += 1
            else:
                outcomes[action][0] += int(row["task_success"])
                outcomes[action][1] += 1
    if set(max_steps) != {"observe", "continue", "retry"}:
        raise ValueError("calibration lacks a supported recovery macro")
    caps = {"observe": Budget(1,0,0,ceil(max_steps["observe"]*1.25)),
            "continue": Budget(0,1,0,ceil(max_steps["continue"]*1.25)),
            "retry": Budget(1,2,0,ceil(max_steps["retry"]*1.25))}
    states = {unknown(f,k) for f in FAMILIES for k in range(4)} | {"ready", "retry_ready"}
    leaf_success = {s: ((leaf[s][0]+.5)/(leaf[s][1]+1) if leaf[s][1] else 0.) for s in states}
    obs_transitions = {}
    permissions = {"ready": frozenset({"continue", "stop"}),
                   "retry_ready": frozenset({"retry", "stop"}),
                   "unknown": frozenset({"observe", "stop"})}
    minima = {"ready": caps["continue"], "retry_ready": caps["retry"]}
    for family in FAMILIES:
        positive = "retry_ready" if family == "precontact" else "ready"
        for k in range(3):
            state = unknown(family,k)
            n = sum(counts[state].values())
            if not n:
                continue  # Never silently fill unobserved histories.
            # Jeffreys smoothing is explicit, not a claim of calibrated confidence.
            p = (counts[state][positive]+.5)/(n+1)
            durations = duration_counts[state]
            pooled = defaultdict(int)
            for bucket in durations.values():
                for duration, count in bucket.items():
                    pooled[duration] += count
            branches = []
            for label, probability in ((positive,p),("unknown",1-p)):
                # For an unobserved smoothed outcome, explicitly use the pooled
                # duration prior; no pilot data or fabricated timing is fitted.
                bucket = durations[label] or pooled
                total = sum(bucket.values())
                for duration,count in sorted(bucket.items()):
                    branches.append(Branch(probability*count/total,label,
                        next_state=positive if label == positive else unknown(family,k+1),
                        resources=Budget(1,0,0,duration)))
            obs_transitions[state] = tuple(branches)
            finish = caps["retry"] if family == "precontact" else caps["continue"]
            minima[state] = Budget(caps["observe"].observations+finish.observations,
                                  finish.commands, 0, min(pooled)+finish.time_units)
    actions = {"observe": ActionModel(caps["observe"], 0., obs_transitions)}
    for action, state in (("continue","ready"), ("retry","retry_ready")):
        good, n = outcomes[action]
        p = (good+.5)/(n+1)
        actions[action] = ActionModel(caps[action], 0., {state: (
            Branch(p,"model_terminal_success",terminal="success"),
            Branch(1-p,"model_terminal_failure",terminal="failure"))})
    model = RecoveryModel(actions, leaf_success, provenance,
        "Jeffreys-smoothed development rule-3 continuation; common to depth1/depth2; budget lower bound enforced",
        permissions, minima)
    return model, caps, {"transition_counts": dict(counts), "macro_success_counts": dict(outcomes),
                         "leaf_counts": dict(leaf), "observed_max_steps": dict(max_steps),
                         "observation_duration_counts": dict(duration_counts),
                         "smoothing": "Jeffreys Beta(0.5,0.5); not uncertainty-robust",
                         "no_hidden_case_features": True}


def choose_rule_limit(model, caps, budgets):
    planner = RecoveryDecisionPlanner(model, depth=4)
    scores = {}
    for threshold in range(4):
        def rule(ctx, allowed):
            return fixed_rule(ctx, allowed, threshold, model.leaf_min_resources)
        scores[threshold] = sum((4 if f == "post_pick" else 1)*planner.evaluate_policy(
            Context("tune", {unknown(f,0):1.}, b, frozenset({"observe","stop"})),rule)
            for f in FAMILIES for b in budgets)
    # A single fixed threshold, not a threshold selected on pilot outcomes.
    best = max(scores, key=lambda k: (scores[k], -k))
    return best, scores


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--calibration", type=Path,
                        help="Reuse complete, fixed development records; never pilot/evaluation records.")
    parser.add_argument("--pilot-seeds",type=int,nargs="+",default=list(PILOT_SEEDS))
    args = parser.parse_args()
    if len(set(args.pilot_seeds)) != len(args.pilot_seeds) or not set(args.pilot_seeds) <= {940,941,942,943}:
        parser.error("pilot seeds must be unique within development reservation 940..943")
    args.output.mkdir(parents=True, exist_ok=False)
    source_files = [Path(__file__), Path("ch3/repair/budgeted_recovery_fixture.py"),
                    Path("ch3/repair/recovery_decision.py")]
    manifest = {"development_seeds": DEVELOPMENT_SEEDS, "pilot_seeds": args.pilot_seeds,
        "cases": CASES, "methods": ["rule","depth1","depth2"],
        "scope": "controlled_sensor_dropout_development_pilot_NOT_formal_NOT_LLM_comparison",
        "reused_calibration": str(args.calibration.resolve()) if args.calibration else None,
        "source_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        "sources_frozen_before_results": True}
    (args.output/"manifest.json").write_text(json.dumps(manifest, indent=2))
    snapshot = args.output/"sources"; snapshot.mkdir()
    for p in source_files:
        (snapshot/p.name).write_bytes(p.read_bytes())
    calibration=[]
    calibration_root = args.calibration if args.calibration else args.output/"calibration"
    for seed in DEVELOPMENT_SEEDS:
        for case in CASES:
            if args.calibration:
                row=json.loads((args.calibration/f"seed_{seed}_{case}"/"result.json").read_text())
                if (row["seed"],row["case"],row["method"]) != (seed,case,"calibrate"):
                    raise ValueError("wrong calibration provenance")
            else:
                row=run_episode(seed,case,calibration_root/f"seed_{seed}_{case}")
            calibration.append(row)
            print(json.dumps({"phase":"calibration","seed":seed,"case":case,"status":row["status"],
                              "success":row["task_success"],"reused":bool(args.calibration)}),flush=True)
    model,caps,statistics=fit_model(calibration, str(calibration_root.resolve()))
    c,o,r = caps["continue"].time_units,caps["observe"].time_units,caps["retry"].time_units
    budgets=[Budget(4,2,0,t) for t in (c,c+2*o,r+3*o)]
    threshold,scores=choose_rule_limit(model,caps,budgets)
    frozen={"caps":{a:asdict(b) for a,b in caps.items()},"budgets":[asdict(b) for b in budgets],
            "rule_threshold":threshold,"rule_tuning_model_values":scores,"statistics":statistics,
            "model":asdict(model),"primary_outcome":"independent_terminal_task_success",
            "limitations":["Small-sample empirical hazard, not validated probability calibration.",
                           "Fault persistence indexed by acquisition count, not continuous physical time.",
                           "Fixed threshold is model-tuned; no claim of best possible rule/tree.",
                           "No LLM direct-replanning baseline or open-world evaluation."]}
    def encode(obj):
        if isinstance(obj, (set, frozenset)):
            return sorted(obj)
        raise TypeError(type(obj).__name__)
    (args.output/"frozen_model.json").write_text(json.dumps(frozen,indent=2,default=encode))
    results=[]
    for seed in args.pilot_seeds:
        for case in CASES:
            for bi,budget in enumerate(budgets):
                methods=["rule","depth1","depth2"]
                if (seed+bi)%2:
                    methods.reverse()
                for method in methods:
                    row=run_episode(seed,case,args.output/"pilot"/f"seed_{seed}_{case}_b{bi}_{method}",
                        model=model,caps=caps,budget=budget,method=method,rule_limit=threshold)
                    row["budget_index"]=bi
                    results.append(row)
                    (args.output/"pilot_results.json").write_text(json.dumps(results,indent=2))
                    print(json.dumps({"phase":"pilot","seed":seed,"case":case,"budget":bi,"method":method,
                        "status":row["status"],"success":row["task_success"],
                        "steps":row.get("recovery_control_steps"),"error":row.get("error")}),flush=True)
    if any(r["status"] != "complete" for r in calibration+results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
