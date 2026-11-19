"""Fixed-goal transfer on new sequences, plus a history-information witness."""
import argparse
from collections import Counter
from pathlib import Path
import platform
import random
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_purpose_repair import SharedReadyCache
from ch3.revision.fragment_runtime import build_fragment_runtime
from scripts.benchmark_architecture_loop import audit, run_loop, semantic_digest
from scripts.benchmark_fragment_purpose_repair import PARTIAL, SOURCE, build_bank
from scripts.benchmark_fragment_three_objects import check, close, digest, read, write


ARMS = ("standard", "fixed", "source_partial", "learned")
PROTOCOL = Path("docs/ch3_fixed_goal_transfer_protocol_20261119.md")


def atom(kind, obj, target):
    return {"kind": kind, "object": obj, "target": target}


def fixtures():
    rng = random.Random(1192026)
    objects = ("blue", "yellow", "red")
    targets = ("table", "green", "return")
    episodes = []
    for ep in range(24):
        revisions = []
        for r in range(10):
            shuffled = rng.sample(objects, 3)
            terminals = rng.sample(targets, 3)
            atoms = [atom("at", obj, target) for obj, target in zip(shuffled, terminals)]
            if r in (2, 5, 8):
                path = rng.sample(targets, 2)
                atoms += [atom("visit", shuffled[0], target) for target in path]
                atoms.append({"kind": "before", "first": f"r{r}-3", "second": f"r{r}-4"})
            elif r:
                visits = rng.sample([(o, t) for o in objects for t in targets], rng.randint(1, 3))
                atoms += [atom("visit", obj, target) for obj, target in visits]
                if rng.random() < .25:
                    atoms.append({"kind": "forbid", "object": rng.choice(objects)})
            revisions.append({"index": r, "atoms": atoms, "first_action_observation":
                ("normal", "no_effect", "foreign_legal_event")[(ep+r) % 3]})
        episodes.append({"id": f"fixed-transfer-{ep:02}", "revisions": revisions})
    return episodes


def history_witness(domain, bridge):
    model = domain.model
    state = model.State(blue="table", yellow="green", red="table")
    obligations = tuple(model.Obligation(model.Clause(**row)) for row in (
        {"id": "first", **atom("visit", "blue", "return")},
        {"id": "second", **atom("visit", "yellow", "green")},
        {"id": "order", "kind": "before", "first": "first", "second": "second"},
        {"id": "finish", **atom("at", "yellow", "green")}))
    task = model.Contract(1, obligations)
    rows = []
    for evidence, expected, monitors in (
            ("confirmed", "ready", [(False, False)]),
            ("refuted", "conflict", [(True, True)]),
            ("unknown", "need_observation", [(True, True), (False, False)])):
        ledger = (model.Event(1, "pick", "blue", None), model.Event(2, "place", "blue", "return", evidence),
                  model.Event(3, "pick", "blue", None), model.Event(4, "place", "blue", "table"),
                  model.Event(5, "pick", "yellow", None), model.Event(6, "place", "yellow", "green"))
        s = model.Snapshot(model.Contract(0, ()), state, ledger, "equal-current-observation")
        interpretations = tuple(model.possible_histories(ledger))
        realized = [(bool(v), bool(p)) for v, p in (model.monitor(task, state, h) for h in interpretations)]
        assert realized == monitors  # committed wrong order cannot be repaired by future suffix
        rt = bridge.Runtime(s)
        result, route = rt.accept(task, expected_digest=s.digest, expected_generation=0)
        reference = model.decide(rt.snapshot, task, 8)
        assert result.status == reference.status == expected
        assert not result.plan
        rows.append({"evidence": evidence, "state": state, "contract": task, "history": ledger,
                     "status": result.status, "reference": reference.status, "route": route,
                     "interpretations": realized})
    return rows


def bootstrap_savings(a, b):
    rng = random.Random(1192027)
    differences = [a[key]-b[key] for key in sorted(a)]
    count = len(differences)
    samples = sorted(sum(rng.choices(differences, k=count))/count for _ in range(5000))
    return {"mean_calls_saved_per_sequence": sum(differences)/count,
            "sequence_bootstrap_95_percentile_interval": [samples[125], samples[4874]],
            "n_sequences": count, "n_resamples": len(samples)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    for folder in (SOURCE, PARTIAL):
        check(folder)
    output.mkdir(parents=True, exist_ok=False)
    files = [PROTOCOL, Path(__file__).resolve().relative_to(Path.cwd()), *Path("ch3/revision").glob("*.py"),
             *[Path("scripts")/name for name in ("benchmark_architecture_loop.py", "benchmark_fragment_purpose_repair.py",
                "benchmark_fragment_execution.py", "benchmark_fragment_three_objects.py")]]
    hashes = {str(path): digest(path) for path in files}
    for path in files:
        target = output/"sources"/path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    episodes = fixtures()
    write(output/"episodes.json", episodes)
    write(output/"plan.json", {"seed": 1192026, "arms": ARMS, "repeats": 8, "source_frozen": True,
        "target_learning": False, "target_repair": False, "new_sequences": len(episodes), "horizon": 8,
        "python": platform.python_version(), "platform": platform.platform()})
    refs, timings, counts, expected, common, first = {}, [], {}, None, None, {}
    for repeat in range(8):
        order = ARMS if repeat < 4 else tuple(reversed(ARMS))
        shift = repeat % 4
        for arm in (*order[shift:], *order[:shift]):
            start = perf_counter()
            d = build_compact_domain()
            lower = IndependentActionBound(d)
            bank = build_bank(d, arm)
            raw_memo = read(SOURCE/"shared_memo.json")
            cache = SharedReadyCache(d, raw_memo)
            session = d.Session(bank)
            session.memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"])) for r in raw_memo}
            session.freeze()
            bridge = build_fragment_runtime(d, session, lower)
            prepared = perf_counter()
            traces = run_loop(d, bridge, session, lower, cache, episodes, "no_repair")
            finished = perf_counter()
            semantic = semantic_digest(traces)
            if expected is None:
                expected = semantic
                write(output/"common_trace.json", traces)
            assert semantic == expected and not session.patterns and len(session.memo) == len(raw_memo)
            assert not any(row["repairs"] for row in traces)
            if repeat == 0:
                checks = audit(d, bridge, traces, refs, {})
                sizes = [len(row["decisions"]) for row in traces]
                assert sum(sizes) == len(bridge.records)
                per_episode = Counter({episode["id"]: 0 for episode in episodes})
                by_order = Counter()
                index = 0
                for row, amount in zip(traces, sizes):
                    records = bridge.records[index:index+amount]
                    per_episode[row["episode"]] += sum(r["origin"] == "backend" for r in records)
                    kind = "ordered" if row["revision"] in (2, 5, 8) else "unordered"
                    by_order[kind] += sum(r["origin"] == "backend" for r in records)
                    index += amount
                assert sum(per_episode.values()) == session.backend_calls
                counts[arm] = dict(per_episode)
                first[arm] = {"checks": checks, "backend_by_contract_kind": dict(by_order),
                              "origins": dict(Counter(row["origin"] for row in bridge.records))}
                write(output/f"{arm}_runtime_decisions.json", bridge.records)
                if arm == "learned":
                    write(output/"history_witness.json", history_witness(d, bridge))
                common = {"requests": len(traces), "completed": sum(r["completed"] for r in traces),
                          "actions": sum(len(r["actions"]) for r in traces), "trace_sha256": semantic}
            sample = {"arm": arm, "repeat": repeat, "calls": session.backend_calls,
                      "prep_ms": 1000*(prepared-start), "loop_ms": 1000*(finished-prepared),
                      "total_ms": 1000*(finished-start)}
            timings.append(sample)
            print(sample, flush=True)
    methods = {arm: {"calls": sum(counts[arm].values()),
                     "medians": {k: median(t[k] for t in timings if t["arm"] == arm)
                                 for k in ("prep_ms", "loop_ms", "total_ms")},
                     **first[arm]} for arm in ARMS}
    clock = {(t["repeat"], t["arm"]): t["total_ms"] for t in timings}
    comparisons = {arm: {**bootstrap_savings(counts[arm], counts["learned"]),
                         "paired_time_saved_ms": [clock[i, arm]-clock[i, "learned"] for i in range(8)]}
                   for arm in ARMS[:-1]}
    prior = read(SOURCE/"timings.json")
    source_ms = {arm: median(t["preparation_ms"] for t in prior if t["method"] == arm) for arm in ("fixed", "learned")}
    source_increment = source_ms["learned"]-source_ms["fixed"]
    saved = median(comparisons["fixed"]["paired_time_saved_ms"])
    write(output/"timings.json", timings)
    write(output/"per_sequence_calls.json", counts)
    write(output/"summary.json", {**common, "unique_reference_snapshots": len(refs), "methods": methods,
          "comparisons_with_learned": comparisons, "prior_source_preparation_median_ms": source_ms,
          "incremental_source_preparation_ms": source_increment,
          "nominal_payback_workload_batches_vs_fixed": max(0, source_increment)/saved if saved > 0 else None,
          "target_contract_revisions_adopted": 0, "model_calls": 0})
    close(output, hashes, {str(p/"manifest.json"): digest(p/"manifest.json") for p in (SOURCE, PARTIAL)})


if __name__ == "__main__":
    main()
