"""Finite-model conditional nogood controls, with explicit partial state facts.

`source` generalizes all renamed source-state fact subsets. `complete` learns
every minimal sufficient conjunction in the same finite state language. Neither
is a reimplementation of the complete COAST/hC/GraphNLP planning algorithms.
The latter is intentionally a representation-equivalence control for full masks.
"""
from dataclasses import asdict
from hashlib import sha256
from itertools import combinations, permutations, product
import json
from pathlib import Path

from ch3.revision.fragments import Atom


def guard_universe(domain):
    masks = {}
    for values in product((None, *domain.model.TARGETS, "hand"), repeat=len(domain.model.OBJECTS)):
        guard = tuple(sorted(Atom("loc", o, v) for o, v in zip(domain.model.OBJECTS, values) if v is not None))
        masks[guard] = sum(1 << i for i, state in enumerate(domain.states)
            if all(getattr(state, a.object) == a.target for a in guard))
    return masks


def minimal_guards(candidates, masks, bad):
    valid = {g for g in candidates if masks[g] and masks[g] & ~bad == 0}
    return tuple(sorted((g for g in valid if not any(g[:i]+g[i+1:] in valid for i in range(len(g)))),
                        key=lambda g: (len(g), g)))


def generate_nogoods(domain, failures, mode, oracle, horizon=8, *, masks=None):
    if mode not in ("source", "complete"):
        raise ValueError("unknown partial nogood scope")
    if type(horizon) is not int or not 0 <= horizon <= 12:
        raise ValueError("invalid nogood horizon")
    masks = guard_universe(domain) if masks is None else masks
    semantics = domain.fingerprint()
    groups = {}
    for failure in failures:
        if failure["horizon"] != horizon or failure["semantics_sha256"] != semantics:
            raise ValueError("failure domain or horizon mismatch")
        atoms = tuple(Atom(**a) for a in failure["core"])
        if not atoms or any(a.kind not in ("at", "visit", "forbid", "empty") for a in atoms):
            raise ValueError("unsupported task core")
        domain.old.contract(atoms)
        state = domain.model.State(**failure["state"]).validate()
        pattern = domain.regions.canonical(atoms, horizon)
        if pattern.key != failure["key"]:
            raise ValueError("source core key mismatch")
        # Validate the original trigger using the same exact oracle; this query
        # is retained and charged, even when later binding profiles reuse it.
        if oracle.solve(atoms, state, horizon).status != "conflict":
            raise ValueError("source core is feasible at its declared state")
        if mode == "complete":
            for binding in domain.regions.assignments(pattern):
                concrete = tuple(sorted(domain.old.instantiate(pattern, binding)))
                groups.setdefault(concrete, {"key": pattern.key, "candidates": set(masks)})
        else:
            for names in permutations(domain.model.OBJECTS):
                rename = dict(zip(domain.model.OBJECTS, names))
                for targets in permutations(domain.model.TARGETS):
                    places = {"hand": "hand", "": "", **dict(zip(domain.model.TARGETS, targets))}
                    concrete = tuple(sorted(Atom(a.kind, rename.get(a.object, ""), places[a.target]) for a in atoms))
                    locations = tuple(sorted(Atom("loc", rename[o], places[getattr(state, o)])
                                             for o in domain.model.OBJECTS))
                    candidates = groups.setdefault(concrete, {"key": pattern.key, "candidates": set()})["candidates"]
                    # Also permits safe partial subsets when the fully renamed
                    # state violates capacity; vacuous subsets never certify.
                    for size in range(len(locations)+1):
                        candidates.update(combinations(locations, size))
    engine = sha256(semantics.encode()+Path(__file__).read_bytes()).hexdigest()
    grouped_records = {}
    for atoms, group in sorted(groups.items()):
        distances = oracle.distances(atoms)
        bad = sum(1 << i for i, length in enumerate(distances) if length < 0 or length > horizon)
        guards = minimal_guards(group["candidates"], masks, bad)
        coverage = 0
        for guard in guards:
            coverage |= masks[guard]
        if mode == "complete" and coverage != bad:
            raise AssertionError("minimal nogoods do not cover complete refusal set")
        record = grouped_records.setdefault(group["key"], {"key": group["key"], "horizon": horizon,
            "mode": mode, "semantics_sha256": semantics, "engine_sha256": engine, "bindings": []})
        record["bindings"].append({"atoms": [asdict(a) for a in atoms],
            "guards": [[[a.object, a.target] for a in guard] for guard in guards],
            "bad_state_mask": coverage})
    return [grouped_records[key] for key in sorted(grouped_records)]


class PartialNogoodBank:
    def __init__(self, domain, fixed_bank, failures, *, mode="source", oracle=None, expected_records=None):
        self.horizon = fixed_bank.horizon
        self._token = domain.regions.runtime_token()
        if fixed_bank._token != self._token:
            raise ValueError("fixed bank is stale or from another domain")
        self.domain, self.mode = domain, mode
        oracle = oracle or domain.old.Oracle()
        masks = guard_universe(domain)
        self.records = generate_nogoods(domain, failures, mode, oracle, self.horizon, masks=masks)
        if expected_records is not None and json.dumps(self.records, sort_keys=True) != json.dumps(expected_records, sort_keys=True):
            raise ValueError("untrusted partial nogoods differ from recomputation")
        self.references = dict(fixed_bank.references)
        candidates = [list(v) for v in fixed_bank.conditions]
        for record in self.records:
            for entry in record["bindings"]:
                bits = domain.regions.task_mask(tuple(Atom(**a) for a in entry["atoms"]))
                for raw in entry["guards"]:
                    guard = tuple(Atom("loc", o, t) for o, t in raw)
                    for index in range(len(domain.states)):
                        if masks[guard] & (1 << index):
                            candidates[index].append(bits)
                            self.references.setdefault((index, bits), {"key": record["key"],
                                "kind": "partial_state_nogood", "scope": mode, "state_guard": raw})
        self.conditions = []
        for entries in candidates:
            reduced = []
            for bits in sorted(set(entries), key=lambda b: (b.bit_count(), b)):
                if not any(previous & bits == previous for previous in reduced):
                    reduced.append(bits)
            self.conditions.append(tuple(reduced))
        self.evidence = domain.regions.RegionBank.evidence.__get__(self, type(self))

    def match(self, snapshot, horizon=8):
        if type(horizon) is not int or not 0 <= horizon <= self.horizon:
            return False
        return self.evidence(snapshot) is not None
