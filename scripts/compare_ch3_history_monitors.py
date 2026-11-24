"""Compare retained-plan monitors on the same symbolic histories and findings."""

import argparse
from collections import Counter
from dataclasses import asdict, replace
from itertools import permutations
import json
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from ch3.revision import evaluation, runtime
from ch3.revision.evidence_resolution import EvidenceFinding, EvidenceResolution
from ch3.revision.fragment_domain_adapter import _class, _function, build_domain
from scripts.profile_ch3_failure_lookup import PhaseClock


HORIZON = 8
ARMS = ("deterministic_present", "deterministic_absent", "wait_relevant_unknown",
        "branch_candidate_validation", "same_history_replan", "routed")


def obligations(m, *items):
    return tuple(m.Obligation(m.Clause(**item)) for item in items)


def visit(id_, obj, target):
    return {"id": id_, "kind": "visit", "object": obj, "target": target}


def fixtures(m):
    families = ("inspection_order", "protected_object", "inspection_already_confirmed", "repeat_inspection")
    for item, shipment, spare in permutations(m.OBJECTS):
        for family in families:
            state = m.State(**({shipment: "return"} if family == "inspection_order" else {}))
            goal = {"id": "finish", "kind": "at", "object": spare, "target": "green"}
            history = [m.Event(1, "place", item, "green", "unknown", "unconfirmed-inspection")]
            clauses = []
            expected = ("ready", "ready", "ready")
            if family == "inspection_order":
                history.append(m.Event(3, "place", shipment, "return", observation_id="shipment-observed"))
                clauses = [visit("check", item, "green"), visit("ship", shipment, "return"),
                           {"id": "order", "kind": "before", "first": "check", "second": "ship"}]
                expected = ("need_observation", "ready", "conflict")
            elif family == "protected_object":
                clauses = [{"id": "protect", "kind": "forbid", "object": item}]
                expected = ("need_observation", "conflict", "ready")
            elif family == "inspection_already_confirmed":
                history.append(m.Event(3, "place", item, "green", observation_id="second-inspection-observed"))
                clauses = [visit("check", item, "green")]
            else:
                goal = {"id": "finish", "kind": "at", "object": item, "target": "return"}
                clauses = [visit("check", item, "green")]
            # Generate the same actual prior plan for all methods, before the
            # added requirement. Only the new decision is part of the timing.
            prior_task = m.Contract(0, obligations(m, goal))
            source = m.Snapshot(prior_task, state, tuple(history), "current-state")
            prior = m.decide(source, prior_task, HORIZON)
            assert prior.status == "ready" and len(prior.plan) == 2
            task = m.Contract(1, obligations(m, goal, *clauses))
            case = f"{family}/{item}-{shipment}-{spare}"
            yield case, family, replace(source, contract=task).validate(), prior.plan, expected


def bind(m):
    evaluation_ns = {**vars(evaluation), **{k: getattr(m, k) for k in
                     ("Contract", "decide", "monitor", "possible_histories", "semantic_key", "successors")}}
    validate = _function(evaluation.valid_suffix, evaluation_ns)
    runtime_ns = {**vars(runtime), "Event": m.Event, "Snapshot": m.Snapshot, "State": m.State}

    class FindingRuntime(EvidenceResolution, _class(runtime.RevisionRuntime, runtime_ns)):
        pass

    return validate, FindingRuntime


def projected(snapshot, assume):
    return replace(snapshot, history=tuple(replace(e, evidence=assume) if e.evidence == "unknown" else e
                                           for e in snapshot.history))


def relevant_unknown(snapshot):
    for event in snapshot.history:
        if event.evidence != "unknown":
            continue
        for obligation in snapshot.contract.obligations:
            c = obligation.clause
            if event.seq <= obligation.since or c.object != event.object:
                continue
            if c.kind == "forbid" or (c.kind == "visit" and event.kind == "place" and c.target == event.target):
                return True
    return False


def decision_functions(m, validate, clock):
    duration_check = clock.measure("suffix_validation", validate)
    full = clock.measure("solver", m.decide)
    routed_ns = {**vars(runtime), "Decision": m.Decision, "valid_suffix": duration_check, "decide": full,
                 "rebase_suffix": _function(runtime.rebase_suffix, {**vars(runtime), "Event": m.Event})}
    routed = _function(runtime.routed_decision, routed_ns)

    def check(s, old):
        return len(old) <= HORIZON and duration_check(s, s.contract, old)

    def keep_or_solve(s, old):
        if check(s, old):
            return "ready", old
        result = full(s, s.contract, HORIZON)
        return result.status, result.plan

    def conservative(s, old):
        if relevant_unknown(s):
            return "need_observation", ()
        return keep_or_solve(projected(s, "refuted"), old)

    def branch_candidate(s, old):
        # Strong history-aware control. Reuse a universal old plan first.
        # Otherwise solve each concrete branch and test the proposed plans
        # jointly, without searching for a different shared suffix.
        if check(s, old):
            return "ready", old
        found_feasible = False
        for branch in m.possible_histories(s.history):
            present = {e.seq for e in branch}
            interpretation = replace(s, history=tuple(
                replace(e, evidence="confirmed" if e.seq in present else "refuted")
                if e.evidence == "unknown" else e for e in s.history))
            outcome = full(interpretation, s.contract, HORIZON)
            if outcome.status == "ready":
                found_feasible = True
                if check(s, outcome.plan):
                    return "ready", outcome.plan
        return ("need_observation" if found_feasible else "conflict"), ()

    def signature(result):
        return result.status, result.plan

    return {
        "deterministic_present": lambda s, p: keep_or_solve(projected(s, "confirmed"), p),
        "deterministic_absent": lambda s, p: keep_or_solve(projected(s, "refuted"), p),
        "wait_relevant_unknown": conservative,
        "branch_candidate_validation": branch_candidate,
        "same_history_replan": lambda s, p: signature(full(s, s.contract, HORIZON)),
        "routed": lambda s, p: signature(routed(s, s.contract, p, HORIZON)[0]),
    }


def rows(fixtures_, finding_runtime, clock):
    resolve = clock.measure("resolve_evidence", finding_runtime.resolve_evidence)
    for case, family, unresolved, old, expected in fixtures_:
        yield case, family, "unknown", unresolved, old, expected[0]
        for verdict, gold in zip(("confirmed", "refuted"), expected[1:]):
            rt = finding_runtime(unresolved)
            finding = EvidenceFinding(1, verdict, "later-observation", unresolved.digest, rt.generation)
            yield case, family, verdict, resolve(rt, finding), old, gold


def score(m, validate, data):
    gold = m.decide(data[3], data[3].contract, HORIZON)
    assert gold.status == data[5], data[:3]
    assert gold.status != "ready" or validate(data[3], data[3].contract, gold.plan)
    return gold.status


def run_arm(arm, m, validate, dataset):
    clock = PhaseClock()
    decide = decision_functions(m, validate, clock)[arm]
    samples = []
    for case, family, stage, s, old, gold in dataset:
        before = perf_counter_ns()
        status, plan = decide(s, old)
        elapsed = perf_counter_ns() - before
        samples.append({"case": case, "family": family, "stage": stage, "expected": gold,
                        "status": status, "valid": status != "ready" or validate(s, s.contract, plan),
                        "elapsed_ns": elapsed, "plan": [(e.kind, e.object, e.target) for e in plan]})
    return samples, {"ns": dict(clock.ns), "calls": dict(clock.calls)}


def counts(samples):
    return {
        "decisions": len(samples),
        "correct": sum(r["status"] == r["expected"] and r["valid"] for r in samples),
        "invalid_release": sum(r["status"] == "ready" and not r["valid"] for r in samples),
        "unnecessary_wait": sum(r["status"] == "need_observation" and r["expected"] == "ready" for r in samples),
        "premature_conflict": sum(r["status"] == "conflict" and r["expected"] != "conflict" for r in samples),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = build_domain().model
    validate, finding_runtime = bind(model)
    fixtures_ = list(fixtures(model))
    dataset = list(rows(fixtures_, finding_runtime, PhaseClock()))
    first, timings, finding_runs = {}, [], []
    for row in dataset:
        score(model, validate, row)
    for repeat in range(12):
        clock = PhaseClock()
        assert list(rows(fixtures_, finding_runtime, clock)) == dataset
        finding_runs.append({"repeat": repeat, "calls": clock.calls["resolve_evidence"],
                             "ms": clock.ns["resolve_evidence"] / 1e6})
        order = ARMS if repeat < 6 else ARMS[::-1]
        shift = repeat % len(ARMS)
        for arm in (*order[shift:], *order[:shift]):
            observed, timing = run_arm(arm, model, validate, dataset)
            semantic = [{k: v for k, v in r.items() if k != "elapsed_ns"} for r in observed]
            assert first.setdefault(arm, semantic) == semantic
            timings.append({"arm": arm, "repeat": repeat, **timing,
                            "decision_total_ms": sum(r["elapsed_ns"] for r in observed) / 1e6})
    summary = {}
    for arm, recorded in first.items():
        timing = [r for r in timings if r["arm"] == arm]
        summary[arm] = {
            "unknown": counts([r for r in recorded if r["stage"] == "unknown"]),
            "resolved": counts([r for r in recorded if r["stage"] != "unknown"]),
            "unknown_by_family": {f: counts([r for r in recorded if r["family"] == f and r["stage"] == "unknown"])
                                  for f in sorted({r["family"] for r in recorded})},
            "solver_calls": timing[0]["calls"].get("solver", 0),
            "median_decision_total_ms": median(r["decision_total_ms"] for r in timing),
        }
    assert summary["routed"]["unknown"]["correct"] == 24
    assert all(r["resolved"]["correct"] == 48 for r in summary.values())
    fixtures_json = [{"case": row[0], "family": row[1], "stage": row[2],
                      "snapshot": asdict(row[3]), "old_plan": [asdict(e) for e in row[4]], "expected": row[5]}
                     for row in dataset]
    report = {"horizon": HORIZON, "dataset": fixtures_json, "summary": summary, "decisions": first,
              "timings": timings, "shared_finding_application": finding_runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, default=lambda obj: obj.model_dump(), indent=2)
        stream.write("\n")
    print({arm: (row["unknown"], row["resolved"]["correct"], row["solver_calls"],
                 round(row["median_decision_total_ms"], 3)) for arm, row in summary.items()})


if __name__ == "__main__":
    main()
