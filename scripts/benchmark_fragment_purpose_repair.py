"""Bounded closing experiment: grouped, purpose-permitted target substitution."""
import argparse
from collections import Counter
from dataclasses import asdict, replace
from itertools import product
import json
from pathlib import Path
import shutil
from statistics import median
from time import perf_counter

from ch3.revision.compact_fragment_certificates import build_compact_domain
from ch3.revision.fragment_domain_adapter import IndependentActionBound
from ch3.revision.fragment_partial_nogoods import PartialNogoodBank
from ch3.revision.fragment_purpose_repair import (
    OptionFeatures, PurposePolicy, SharedReadyCache, admissible, candidates, propose)
from scripts.analyze_fragment_execution import decode
from scripts.benchmark_fragment_repair import SOURCE, TARGET, cases, validate_plan
from scripts.benchmark_fragment_repair_frozen import FIXTURES, STATIC
from scripts.benchmark_fragment_three_objects import ROOT, check, close, digest, read, write

PROTOCOL = Path("docs/fragment_purpose_repair_protocol_20261117.md")
PARTIAL = ROOT / "fragment_partial_nogood_comparison_20261117_v1"
ARMS = ("standard", "fixed", "source_partial", "learned", "compiled")


def fixture_policy(domain, snapshot):
    """Explicit SYNTHETIC routing configuration, not inferred user permission."""
    return PurposePolicy("synthetic-object-specific-approved-unit-buffers-v1", snapshot.digest,
                         tuple(domain.model.OBJECTS))


def policy_from(raw):
    return PurposePolicy(raw["id"], raw["snapshot_digest"], tuple(raw["swappable_objects"]),
                         tuple(raw["equivalent_targets"]))


def signature(result):
    return (result.status, result.cost, tuple((e.id, e.target) for e in result.edits))


def reference_rows(domain, snapshot, policy):
    """Independent policy enumeration and the EXISTING replacement operation."""
    m = domain.model
    policy.validate(domain, snapshot)
    blocked = set()
    for obligation in snapshot.contract.obligations:
        c = obligation.clause
        if c.kind != "visit" or c.target not in policy.equivalent_targets:
            continue
        for event in snapshot.history:
            if (event.seq > obligation.since and event.kind == "place" and event.object == c.object
                    and event.target == c.target and event.evidence in ("confirmed", "unknown")):
                blocked.add(c.object)
    choices = []
    seen = set()
    for selected in product((False, True), repeat=len(policy.swappable_objects)):
        chosen = {o for o, bit in zip(policy.swappable_objects, selected) if bit}
        if chosen & blocked:
            continue
        clauses, changes = [], []
        for obligation in snapshot.contract.obligations:
            c = obligation.clause
            if c.kind in ("at", "visit") and c.object in chosen and c.target in policy.equivalent_targets:
                target = policy.equivalent_targets[1 - policy.equivalent_targets.index(c.target)]
                c = m.Clause(**{**c.model_dump(), "target": target})
                changes.append((c.id, target))
            clauses.append(c)
        edits = tuple(sorted(changes))
        if edits in seen:
            continue
        seen.add(edits)
        replacement = m.Replacement(version=snapshot.contract.version, snapshot=snapshot.digest, clauses=clauses)
        task = m.apply_replacement(snapshot, replacement)
        choices.append((len(edits), edits, task))
    return sorted(choices, key=lambda row: row[:2])


def reference(suites, output):
    d = build_compact_domain()
    gold, audits = {}, {}
    for suite, raw_cases in suites.items():
        answers, calls, candidate_checks = {}, 0, 0
        for raw in raw_cases:
            s = decode(d, raw["snapshot"])
            policy = policy_from(raw["policy"])
            rows = reference_rows(d, s, policy)
            actual = candidates(d, s, policy)
            assert [(r.cost, tuple((e.id, e.target) for e in r.edits), r.contract) for r in actual] == rows
            assert all(admissible(d, s, policy, task) for _, _, task in rows)
            candidate_checks += len(rows)
            answer = ("no_permitted_substitution", None, ())
            for cost, edits, task in rows:
                result = d.model.decide(s, task, 8)
                calls += 1
                if result.status == "ready":
                    validate_plan(d, s, task, result.plan)
                    answer = ("proposal", cost, edits)
                    break
            answers[raw["id"]] = answer
        gold[suite] = answers
        audits[suite] = {"unpruned_reference_calls": calls, "all_policy_candidates_checked": candidate_checks}
        write(output / f"{suite}_reference.json", answers)
        print(json.dumps({"phase": "reference", "suite": suite, **audits[suite]}), flush=True)
    return gold, audits


def build_bank(domain, arm):
    if arm == "standard":
        return domain.regions.RegionBank([])
    if arm in ("learned", "compiled"):
        return domain.regions.RegionBank(read(SOURCE / "learned_certificates.json"))
    fixed = domain.regions.RegionBank(read(SOURCE / "fixed_certificates.json"))
    if arm == "fixed":
        return fixed
    return PartialNogoodBank(domain, fixed, read(PARTIAL / "partial_source_source_failures.json"), mode="source")


def feature_audit(suites, output):
    d = build_compact_domain()
    learned = build_bank(d, "learned")
    fixed = build_bank(d, "fixed")
    complete = PartialNogoodBank(d, fixed, read(PARTIAL / "partial_source_source_failures.json"), mode="complete")
    assert complete.conditions == learned.conditions
    counts = Counter()
    for suite, raw_cases in suites.items():
        for raw in raw_cases:
            s = decode(d, raw["snapshot"])
            p = policy_from(raw["policy"])
            compiled = OptionFeatures(d, learned, s, p)
            initially_blocked = learned.evidence(s) is not None
            for row in candidates(d, s, p):
                current = replace(s, contract=row.contract)
                features, state_index = d.features(current)
                hit = learned.evidence(current) is not None
                assert compiled.feature_mask(row.swapped_objects) == features
                assert state_index == compiled.state_index
                assert compiled.blocked(row.swapped_objects) == hit
                counts[suite + "_candidates"] += 1
                counts[suite + "_newly_introduced_rejection"] += hit and not initially_blocked
    audit = {"complete_partial_nogoods_equal_learned_all_34_states": True,
        "all_option_features_and_rejections_equal_original": True, "counts": dict(counts)}
    write(output / "feature_audit.json", audit)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = (SOURCE, TARGET, FIXTURES, STATIC, PARTIAL)
    for folder in inputs:
        check(folder)
    args.output.mkdir(parents=True, exist_ok=False)
    tracked = [PROTOCOL, Path(__file__).resolve().relative_to(Path.cwd()),
        Path("tests/test_fragment_purpose_repair.py"), Path("scripts/analyze_fragment_execution.py"),
        Path("scripts/benchmark_fragment_repair.py"), Path("scripts/benchmark_fragment_repair_frozen.py"),
        Path("scripts/benchmark_fragment_three_objects.py"), *sorted(Path("ch3/revision").glob("*.py"))]
    hashes = {str(p): digest(p) for p in tracked}
    for path in tracked:
        destination = args.output / "sources" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    labels = read(STATIC / "reference.json")
    static = read(FIXTURES / "target_cases.json")
    assert set(labels) == {r["id"] for r in static}
    suites = {"execution": cases(), "static": [dict(r, stage="static_task")
              for r in static if labels[r["id"]] != "ready"]}
    d = build_compact_domain()
    for raw_cases in suites.values():
        for raw in raw_cases:
            raw["policy"] = asdict(fixture_policy(d, decode(d, raw["snapshot"])))
    write(args.output / "fixtures.json", suites)
    write(args.output / "plan.json", {"arms": ARMS, "repeats": 3, "horizon": 8,
        "suite_sizes": {k: len(v) for k, v in suites.items()}, "protocol_sha256": digest(PROTOCOL),
        "explicit_synthetic_purpose_policy": True, "clauses_may_be_deleted": False,
        "all_methods_share_source_ready_cache": True, "source_learning_cost_included": False})
    print(json.dumps({"phase": "frozen", "sizes": {k: len(v) for k, v in suites.items()}}), flush=True)
    gold, reference_audits = reference(suites, args.output)
    audit = feature_audit(suites, args.output)
    timings, first = [], {}
    for repeat in range(3):
        order = ARMS[repeat:] + ARMS[:repeat]
        if repeat == 1:
            order = tuple(reversed(order))
        for suite, raw_cases in suites.items():
            for arm in order:
                started = perf_counter()
                d = build_compact_domain()
                lower = IndependentActionBound(d)
                cache = SharedReadyCache(d, read(SOURCE / "shared_memo.json"))
                bank = build_bank(d, arm)
                decoded = [(raw, decode(d, raw["snapshot"]), policy_from(raw["policy"])) for raw in raw_cases]
                prepared = perf_counter()
                method = arm if arm in ("standard", "compiled") else "recheck"
                records = [{"id": raw["id"], "stage": raw["stage"],
                    "result": propose(d, s, p, bank, lower, cache, method=method, known_nonready=True)}
                    for raw, s, p in decoded]
                finished = perf_counter()
                stats = Counter()
                for (raw, s, p), row in zip(decoded, records):
                    result = row["result"]
                    assert signature(result) == gold[suite][raw["id"]]
                    assert result.snapshot_digest == s.digest and result.policy_id == p.id
                    stats.update(result.stats)
                    if result.status == "proposal":
                        assert admissible(d, s, p, result.contract)
                        assert len(result.edits) == result.cost and len(result.contract.obligations) == len(s.contract.obligations)
                        validate_plan(d, s, result.contract, result.plan)
                row = {"repeat": repeat, "suite": suite, "arm": arm, "stats": dict(stats),
                    "prep_ms": (prepared-started)*1000, "repair_ms": (finished-prepared)*1000,
                    "total_ms": (finished-started)*1000}
                timings.append(row)
                if repeat == 0:
                    first[suite, arm] = records
                    write(args.output / f"{suite}_{arm}_proposals.json", records)
                print(json.dumps(row), flush=True)
    methods, outcomes, increments = {}, {}, {}
    for suite, raw_cases in suites.items():
        methods[suite] = {}
        for arm in ARMS:
            rows = [r for r in timings if r["suite"] == suite and r["arm"] == arm]
            assert all(r["stats"] == rows[0]["stats"] for r in rows)
            methods[suite][arm] = {"stats": rows[0]["stats"], "medians": {
                k: median(r[k] for r in rows) for k in ("prep_ms", "repair_ms", "total_ms")}}
        assert methods[suite]["compiled"]["stats"]["backend_calls"] == methods[suite]["learned"]["stats"]["backend_calls"]
        outcomes[suite] = {"statuses": dict(Counter(v[0] for v in gold[suite].values())),
            "repair_costs": dict(Counter(str(v[1]) for v in gold[suite].values())),
            "by_stage": {stage: dict(Counter(gold[suite][r["id"]][0] for r in raw_cases if r["stage"] == stage))
                         for stage in sorted({r["stage"] for r in raw_cases})}}
        difference = Counter()
        for full, old, partial in zip(first[suite, "learned"], first[suite, "fixed"], first[suite, "source_partial"]):
            assert full["id"] == old["id"] == partial["id"]
            get = lambda row: row["result"].stats["backend_calls"]
            difference["source_saved_vs_fixed_" + full["stage"]] += get(old)-get(full)
            difference["full_saved_vs_source_partial_" + full["stage"]] += get(partial)-get(full)
        increments[suite] = dict(difference)
    summary = {"suite_sizes": {k: len(v) for k, v in suites.items()}, "outcomes": outcomes,
        "all_answers_exact_minimum_and_identical": True, "all_proposals_policy_admissible": True,
        "all_hard_clauses_and_historical_commitments_preserved": True,
        "no_requirement_deletion": True, "methods": methods, "increments_by_stage": increments,
        "reference_audits": reference_audits, "feature_audit": audit,
        "source_learning_cost_included": False, "model_calls": 0, "physical_execution": False}
    write(args.output / "timings.json", timings)
    write(args.output / "summary.json", summary)
    close(args.output, hashes, {str(f / "manifest.json"): digest(f / "manifest.json") for f in inputs})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
