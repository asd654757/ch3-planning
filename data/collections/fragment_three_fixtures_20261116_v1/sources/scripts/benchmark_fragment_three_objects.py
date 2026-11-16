"""Prospectively split three-object scale/structure audit of frozen algorithms."""
import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from itertools import permutations
import json
from pathlib import Path
import random
import shutil
from statistics import median
from time import perf_counter

from ch3.context_memory.cases import load_cases as old_load
from ch3.revision.fragments import Atom, Pattern
from ch3.revision.fragment_domain_adapter import build_domain, IndependentActionBound
from ch3.revision.fragment_session import Resolution
from scripts.benchmark_revision_recovery_pilot import write

ROOT = Path("data/collections")
PROTOCOL = Path("docs/fragment_three_objects_protocol_20261116.md")
PRIOR = ROOT / "fragment_online_source_20261116_v1"


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def archive(output):
    files = [Path(__file__).resolve().relative_to(Path.cwd()), PROTOCOL,
             *[Path("ch3/revision") / n for n in ("history.py", "fragments.py", "fragment_regions.py",
               "fragment_session.py", "fragment_domain_adapter.py")],
             Path("tests/test_fragment_domain_adapter.py")]
    hashes = {str(p): digest(p) for p in files}
    for p in files:
        dest = output / "sources" / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    return hashes


def close(output, hashes, inputs=None, extra=None):
    assert all(digest(p) == h for p, h in hashes.items())
    write(output / "manifest.json", {"source_hashes": hashes, "input_hashes": inputs or {},
          "artifact_hashes": {p.name: digest(p) for p in output.glob("*.json")}, **(extra or {})})


def check(path):
    m = read(path / "manifest.json")
    for f, h in {**m["source_hashes"], **m["input_hashes"]}.items():
        assert digest(f) == h, f
    for f, h in m["artifact_hashes"].items():
        assert digest(path / f) == h, f


def load(domain, path):
    result = []
    m = domain.model
    for raw in read(path):
        s = raw["snapshot"]
        task = m.Contract(s["contract"]["version"], tuple(m.Obligation(m.Clause(**o["clause"]), o["since"])
                          for o in s["contract"]["obligations"]))
        snap = m.Snapshot(task, m.State(**s["state"]), tuple(m.Event(**e) for e in s["history"]), s["observation_id"]).validate()
        result.append({**raw, "snapshot": snap})
    return result


def structural_signature(d, snap):
    mask, _ = d.features(snap)
    atoms = [a for i, a in enumerate(d.regions.POOL) if mask & (1 << i)]
    forms = []
    for object_order in permutations(d.model.OBJECTS):
        names = dict(zip(d.model.OBJECTS, object_order))
        for targets in (("green", "return"), ("return", "green")):
            places = {"table": "table", "hand": "hand", **dict(zip(("green", "return"), targets))}
            task = tuple(sorted((a.kind, names.get(a.object, ""), places.get(a.target, "")) for a in atoms))
            state = tuple(sorted((names[o], places[getattr(snap.state, o)]) for o in d.model.OBJECTS))
            forms.append((task, state))
    return repr(min(forms))


def generate(d, n, sizes, rng, seen, group):
    result = []
    terminal_states = [s for s in d.states if s.holding is None]
    visits = [Atom("visit", o, t) for o in d.model.OBJECTS for t in d.model.TARGETS]
    attempts = 0
    while len(result) < n:
        attempts += 1
        if attempts > 100000:
            raise RuntimeError("fixed structural sample space exhausted")
        size = rng.choice(sizes)
        goals = rng.randint(1, min(3, size))
        terminal = rng.choice(terminal_states)
        atoms = {Atom("at", o, getattr(terminal, o)) for o in rng.sample(list(d.model.OBJECTS), goals)}
        extras = list(visits)
        if rng.random() < .15:
            extras.append(Atom("forbid", rng.choice(d.model.OBJECTS)))
        if rng.random() < .20:
            extras.append(Atom("empty"))
        atoms.update(rng.sample(extras, size - len(atoms)))
        if {a.object for a in atoms if a.object} != set(d.model.OBJECTS):
            continue
        s = d.model.Snapshot(d.old.contract(atoms), rng.choice(d.states), (), "three-object-fixture").validate()
        sig = structural_signature(d, s)
        if sig in seen:
            continue
        seen.add(sig)
        result.append({"id": f"{group}-{len(result):04}", "group": group,
                       "snapshot": s, "structural_signature": sig, "raw_clauses": len(atoms)})
    return result


def fixtures(output):
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    d = build_domain()
    seen = set()
    source = generate(d, 384, (3, 4), random.Random(11671), seen, "source")
    target_rng = random.Random(11672)
    target = generate(d, 384, (3, 4), target_rng, seen, "heldout-small")
    target += generate(d, 384, (5, 6), target_rng, seen, "heldout-large")
    assert len(seen) == 1152
    write(output / "source_cases.json", source)
    write(output / "target_cases.json", target)
    write(output / "summary.json", {"source": len(source), "target": len(target),
          "legal_states": len(d.states), "unique_structural_signatures": len(seen),
          "labels_computed": False, "unchanged_algorithms": d.unchanged_algorithms,
          "domain_fingerprint": d.fingerprint(), "model_calls": 0, "physical_execution": False})
    close(output, hashes)
    print(json.dumps(read(output / "summary.json")), flush=True)


def verify_plan(d, s, resolution):
    if resolution.status != "ready":
        assert not resolution.plan
        return
    assert len(resolution.plan) <= 8
    state = s.state
    for i, event in enumerate(resolution.plan, 1):
        assert event.seq == s.cursor + i
        state = next(ns for ns, e in d.model.successors(state, event.seq) if e == event)
    assert d.model.monitor(s.contract, state, s.history + resolution.plan) == (frozenset(), frozenset())


def verify_two_object_archive(output):
    from ch3.revision.fragment_regions import features as original_features
    d = build_domain(("blue", "yellow"))
    path = ROOT / "revision_fragments_20261116_v1"
    cases = old_load(path / "cases.json")
    converted = load(d, path / "cases.json")
    reference = {r["id"]: r["reference_status"] for r in read(path / "results.json")}
    for old, case in zip(cases, converted):
        s = case["snapshot"]
        assert d.features(s) == original_features(old["snapshot"])
        result = d.model.decide(s, s.contract, 8)
        assert result.status == reference[case["id"]]
        # Static ready traces can be replayed without evidence branching.
        if not s.history:
            verify_plan(d, s, result)
    write(output / "two_object_compatibility.json", {"n": len(cases), "all_statuses_and_features_match": True,
          "original_input_sha256": digest(path / "cases.json"), "reference_sha256": digest(path / "results.json")})


def resolve(session, lower, s):
    if lower.match(s, 8):
        return Resolution("conflict", (), "independent_action_bound")
    return session.resolve(s)


def train(fixtures_path, output):
    check(fixtures_path)
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    verify_two_object_archive(output)
    d = build_domain()
    start = perf_counter()
    cases = load(d, fixtures_path / "source_cases.json")
    source_load_ms = (perf_counter() - start) * 1000
    prior_manifest = read(PRIOR / "manifest.json")
    assert digest(PRIOR / "learned_certificates.json") == prior_manifest["artifact_hashes"]["learned_certificates.json"]
    patterns = []
    for cert in read(PRIOR / "learned_certificates.json"):
        p = cert["pattern"]
        patterns.append(Pattern(tuple(Atom(**a) for a in p["atoms"]),
                                tuple(tuple(v) for v in p["variables"]), p["horizon"]))
    start = perf_counter()
    oracle = d.old.Oracle()
    bank = d.regions.RegionBank.verified([], oracle)
    for i, p in enumerate(patterns):
        bank = d.regions.RegionBank.combine(bank, d.regions.RegionBank.verified([p], oracle))
        print(json.dumps({"old_pattern_certified": i + 1, "of": len(patterns), "oracle_calls": oracle.calls}), flush=True)
    build_ms = (perf_counter() - start) * 1000
    start = perf_counter()
    lower = IndependentActionBound(d)
    lower_build_ms = (perf_counter() - start) * 1000
    sessions, records = {}, {}
    for name in ("fixed", "learned"):
        session = d.Session(bank, learn=name == "learned")
        started = perf_counter()
        results = []
        for case in cases:
            result = resolve(session, lower, case["snapshot"])
            results.append(result)
            if len(session.patterns) >= 8:
                session.learning = False
        elapsed = (perf_counter() - started) * 1000
        for c, result in zip(cases, results):
            verify_plan(d, c["snapshot"], result)
        write(output / f"{name}_source_trace.json", [{"id": c["id"], "status": r.status,
              "origin": r.origin, "new_pattern": r.new_pattern} for c, r in zip(cases, results)])
        write(output / f"{name}_certificates.json", session.bank.certificates)
        write(output / f"{name}_new_patterns.json", list(session.patterns.values()))
        records[name] = {"source_processing_ms": elapsed, "full_task_backend_calls": session.backend_calls,
                         "learning_oracle_calls": session.oracle.calls, "new_patterns": len(session.patterns),
                         "three_object_patterns": sum(sum(sort == "object" for _, sort in p.variables) == 3
                                                       for p in session.patterns.values()),
                         "origins": Counter(r.origin for r in results),
                         "statuses": Counter(r.status for r in results)}
        sessions[name] = session
        print(json.dumps({"method": name, **records[name]}), flush=True)
    a, b = read(output / "fixed_source_trace.json"), read(output / "learned_source_trace.json")
    assert all(x["status"] == y["status"] for x, y in zip(a, b))
    started = perf_counter()
    shared = dict(sessions["fixed"].memo)
    for key, value in sessions["learned"].memo.items():
        if key in shared:
            assert shared[key][0] == value[0]
        else:
            shared[key] = value
    merge_ms = (perf_counter() - started) * 1000
    write(output / "shared_memo.json", [{"key": list(key), "status": status, "plan": [asdict(e) for e in plan]}
          for key, (status, plan) in sorted(shared.items())])
    common = source_load_ms + build_ms + lower_build_ms + merge_ms
    for record in records.values():
        record["preparation_ms"] = common + record["source_processing_ms"]
    write(output / "summary.json", {"n": len(cases), "methods": records,
          "initial_common_certification_calls": oracle.calls, "initial_common_build_ms": build_ms,
          "lower_bound_build_ms": lower_build_ms, "shared_memo_keys": len(shared),
          "target_files_read": False, "source_learning_once": True,
          "unchanged_algorithms": d.unchanged_algorithms, "model_calls": 0, "physical_execution": False})
    close(output, hashes, {str(fixtures_path / "manifest.json"): digest(fixtures_path / "manifest.json"),
          str(PRIOR / "learned_certificates.json"): digest(PRIOR / "learned_certificates.json")})


def validate(fixtures_path, training, output):
    check(fixtures_path)
    check(training)
    output.mkdir(parents=True, exist_ok=False)
    hashes = archive(output)
    d = build_domain()
    cases = load(d, fixtures_path / "target_cases.json")
    source_signatures = {r["structural_signature"] for r in read(fixtures_path / "source_cases.json")}
    assert all(structural_signature(d, c["snapshot"]) == c["structural_signature"]
               and c["structural_signature"] not in source_signatures for c in cases)
    reference = {}
    for i, case in enumerate(cases):
        s = case["snapshot"]
        result = d.model.decide(s, s.contract, 8)
        verify_plan(d, s, result)
        reference[case["id"]] = result.status
        if (i + 1) % 128 == 0:
            print(json.dumps({"reference_completed": i + 1}), flush=True)
    write(output / "reference.json", reference)
    lower = IndependentActionBound(d)
    start = perf_counter()
    memo = {tuple(r["key"]): (r["status"], tuple(d.model.Event(**e) for e in r["plan"]))
            for r in read(training / "shared_memo.json")}
    memo_load_ms = (perf_counter() - start) * 1000
    sessions, loads, recert_calls = {}, {}, {}
    for name in ("fixed", "learned"):
        raw = read(training / f"{name}_certificates.json")
        start = perf_counter()
        session = d.Session(d.regions.RegionBank(raw))
        session.memo = dict(memo)
        session.freeze()
        loads[name] = (perf_counter() - start) * 1000
        recert_calls[name] = sum(len(b["states"]) for c in raw for b in c["bindings"])
        sessions[name] = session
        print(json.dumps({"method_loaded": name, "recertification_ms": loads[name],
                          "recertification_calls": recert_calls[name]}), flush=True)
    timings, first_results = [], {}
    for repeat in range(3):
        indices = list(range(len(cases)))
        random.Random(11673 + repeat).shuffle(indices)
        for name in (("fixed", "learned") if repeat % 2 == 0 else ("learned", "fixed")):
            session = sessions[name]
            before = session.backend_calls
            start = perf_counter()
            results = [resolve(session, lower, cases[i]["snapshot"]) for i in indices]
            elapsed = (perf_counter() - start) * 1000
            ordered = [None] * len(cases)
            for index, result in zip(indices, results):
                assert result.status == reference[cases[index]["id"]]
                verify_plan(d, cases[index]["snapshot"], result)
                ordered[index] = result
            assert len(session.memo) == len(memo) and not session.patterns
            first_results.setdefault(name, ordered)
            row = {"repeat": repeat, "method": name, "wall_ms": elapsed,
                   "backend_calls": session.backend_calls - before,
                   "origins": Counter(r.origin for r in results),
                   "ready_plans_replayed": sum(r.status == "ready" for r in results)}
            timings.append(row)
            write(output / "timings.json", timings)
            print(json.dumps(row), flush=True)
    extra = []
    groups = {}
    for i, case in enumerate(cases):
        a, b = first_results["fixed"][i], first_results["learned"][i]
        group = groups.setdefault(case["group"], {"n": 0, "fixed_backend": 0, "learned_backend": 0,
                                                  "statuses": Counter()})
        group["n"] += 1
        group["fixed_backend"] += a.origin == "backend"
        group["learned_backend"] += b.origin == "backend"
        group["statuses"][reference[case["id"]]] += 1
        if a.origin == "backend" and b.origin == "certified_core":
            evidence = sessions["learned"].bank.evidence(case["snapshot"])
            assert reference[case["id"]] == "conflict"
            extra.append({"id": case["id"], "evidence": evidence})
    source_summary = read(training / "summary.json")
    methods = {}
    for name in sessions:
        rows = [r for r in timings if r["method"] == name]
        prep = source_summary["methods"][name]["preparation_ms"] + loads[name] + memo_load_ms
        query = median(r["wall_ms"] for r in rows)
        methods[name] = {"median_query_ms": query, "preparation_ms": prep,
                         "total_algorithm_ms_estimate": prep + query,
                         "target_backend_calls": rows[0]["backend_calls"],
                         "source_backend_calls": source_summary["methods"][name]["full_task_backend_calls"],
                         "initial_certification_calls": source_summary["initial_common_certification_calls"],
                         "learning_oracle_calls": source_summary["methods"][name]["learning_oracle_calls"],
                         "reload_certification_calls": recert_calls[name]}
    write(output / "additional_coverage.json", extra)
    write(output / "summary.json", {"n": len(cases), "methods": methods, "groups": groups,
          "additional_backend_calls_avoided": len(extra), "reference_statuses": Counter(reference.values()),
          "false_rejections": 0, "source_target_canonical_overlap": 0,
          "source_learning_once": True, "target_learning": False, "target_cache_growth": False,
          "model_calls": 0, "physical_execution": False})
    check(fixtures_path)
    check(training)
    close(output, hashes, {str(fixtures_path / "manifest.json"): digest(fixtures_path / "manifest.json"),
                          str(training / "manifest.json"): digest(training / "manifest.json")})
    print(json.dumps(read(output / "summary.json")), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", choices=("fixtures", "source", "validation"), required=True)
    p.add_argument("--fixtures", type=Path)
    p.add_argument("--training", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.phase == "fixtures":
        fixtures(args.output)
    elif args.fixtures is None:
        p.error("source and validation require --fixtures")
    elif args.phase == "source":
        train(args.fixtures, args.output)
    elif args.training is None:
        p.error("validation requires --training")
    else:
        validate(args.fixtures, args.training, args.output)


if __name__ == "__main__":
    main()
