"""Hand-built design counterexamples, NOT a robot/model benchmark or new algorithm.

The finite exact decision-tree reference uses known uniform synthetic priors,
deterministic static tests and enumerated acceptable decisions. It deliberately
does not model physical sensor accuracy or claim an innovation over DRD/POMDPs.
"""
import argparse
from functools import lru_cache
from itertools import combinations, product
import json
from math import inf, isinf, log2
from pathlib import Path


def common_decisions(worlds, allowed):
    return set.intersection(*(set(allowed[w]) for w in worlds))


def exact_expected_cost(worlds, allowed, queries):
    """A standard exhaustive Bellman reference, for static finite toys only."""
    @lru_cache(None)
    def value(active, available):
        if common_decisions(active, allowed):
            return 0., None
        best, selected = inf, None
        for qid in available:
            query = queries[qid]
            if not query.get("available", True):
                continue
            branches = {}
            for w in active:
                branches.setdefault(query["answers"][w], []).append(w)
            if len(branches) == 1:
                continue
            remaining = tuple(q for q in available if q != qid)
            cost = query["cost"] + sum(len(ws)/len(active)*value(tuple(ws), remaining)[0]
                                       for ws in branches.values())
            if cost < best:
                best, selected = cost, qid
        return best, selected
    return value(tuple(worlds), tuple(queries))


def information_gain(worlds, answers):
    groups = {}
    for w in worlds:
        groups[answers[w]] = groups.get(answers[w], 0)+1
    return -sum((n/len(worlds))*log2(n/len(worlds)) for n in groups.values())


def counterexamples():
    examples = []
    worlds = tuple(product((0, 1), range(8)))
    allowed = {w: {"decision-"+str(w[0])} for w in worlds}
    queries = {
        "irrelevant_context": {"cost": 1, "answers": {w: w[1] for w in worlds}},
        "task_fact": {"cost": 1, "answers": {w: w[0] for w in worlds}},
    }
    gain = {q: information_gain(worlds, t["answers"]) for q, t in queries.items()}
    optimum, first = exact_expected_cost(worlds, allowed, queries)
    assert gain == {"irrelevant_context": 3., "task_fact": 1.} and (optimum, first) == (1., "task_fact")
    examples.append({"id": "irrelevant_information", "entropy_bits": gain,
        "entropy_greedy_total_cost": 2, "exact_decision_cost": optimum,
        "lesson": "More world information need not reduce decision cost."})

    worlds = (0, 1)
    allowed = {0: {"left"}, 1: {"right"}}
    queries = {"expensive": {"cost": 10, "answers": {0: 0, 1: 1}},
               "cheap": {"cost": 1, "answers": {0: 0, 1: 1}}}
    optimum, first = exact_expected_cost(worlds, allowed, queries)
    assert (optimum, first) == (1., "cheap")
    examples.append({"id": "unequal_query_cost", "both_query_cardinalities": 1,
        "costs": {q: t["cost"] for q, t in queries.items()}, "exact_first_query": first,
        "lesson": "Minimum number of queries is not minimum acquisition cost."})

    worlds = tuple(product((0, 1), repeat=2))
    allowed = {w: {"go" if all(w) else "stop"} for w in worlds}
    queries = {"x": {"cost": 1, "answers": {w: w[0] for w in worlds}},
               "y": {"cost": 1, "answers": {w: w[1] for w in worlds}}}
    optimum, _ = exact_expected_cost(worlds, allowed, queries)
    assert optimum == 1.5
    examples.append({"id": "adaptive_branching", "smallest_fixed_query_set_cost": 2,
        "exact_adaptive_expected_cost": optimum,
        "lesson": "A first negative answer can make the second test unnecessary."})

    worlds = (0, 1, 2)
    allowed = {0: {"A", "B"}, 1: {"B", "C"}, 2: {"B"}}
    canonical = [sorted(allowed[w])[0] for w in worlds]
    optimum, _ = exact_expected_cost(worlds, allowed, {})
    assert len(set(canonical)) == 2 and common_decisions(worlds, allowed) == {"B"} and optimum == 0
    examples.append({"id": "overlapping_valid_decisions", "arbitrary_canonical_decisions": canonical,
        "common_valid_decisions": ["B"], "necessary_queries": 0,
        "lesson": "Different selected plans do not imply that evidence must be queried."})

    allowed = {0: {"A", "B"}, 1: {"B", "C"}, 2: {"A", "C"}}
    assert all(set(allowed[a]) & set(allowed[b]) for a, b in combinations(worlds, 2))
    optimum, _ = exact_expected_cost(worlds, allowed, {})
    assert not common_decisions(worlds, allowed) and isinf(optimum)
    examples.append({"id": "pairwise_agreement_is_insufficient", "every_pair_has_common_decision": True,
        "global_common_decisions": [], "decision_without_more_evidence": "unresolved",
        "lesson": "Pairwise compatibility is insufficient for an overlapping decision region."})

    worlds = (0, 1)
    allowed = {0: {"continue"}, 1: {"stop"}}
    for query in ({"cost": 1, "available": False, "answers": {0: 0, 1: 1}},
                  {"cost": 1, "answers": {0: "unknown", 1: "unknown"}}):
        assert isinf(exact_expected_cost(worlds, allowed, {"old_frame": query})[0])
    examples.append({"id": "unavailable_or_uninformative_evidence", "outcome": "unresolved",
        "lesson": "Neither absent archives nor unknown answers provide a truth label."})
    return examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    examples = counterexamples()
    args.output.write_text(json.dumps({"scope": "six_author_built_design_counterexamples",
        "physical_trials": 0, "model_calls": 0, "training": False,
        "not_evidence_of_new_method_superiority": True, "examples": examples}, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"counterexamples_checked": len(examples), "model_calls": 0, "physical_trials": 0}))


if __name__ == "__main__":
    main()
