"""Certified maximal state guards for reusable bounded conflict cores.

All generalization stays inside the registered 13-state model. The compiler
stores state conditions for small task cores, not answers for whole test tasks.
It can fall back to the unchanged planner and cannot certify open-world facts.
"""
from dataclasses import asdict
from hashlib import sha256
from itertools import permutations
import json
from pathlib import Path
from types import MappingProxyType

from ch3.revision import history as model
from ch3.revision import fragments as old
from ch3.revision.fragments import Atom, Pattern, states

STATES = states()
STATE_INDEX = MappingProxyType({(s.blue, s.yellow): i for i, s in enumerate(STATES)})
POOL = tuple(Atom(k, o, t) for k in ("at", "visit") for o in model.OBJECTS for t in model.TARGETS) + (
    Atom("forbid", "blue"), Atom("forbid", "yellow"), Atom("empty"))
BITS = MappingProxyType({(a.kind, a.object, a.target): 1 << i for i, a in enumerate(POOL)})
_CERTIFIED = object()


def runtime_token():
    names = ("decide", "_search", "successors", "monitor", "possible_histories")
    functions = tuple(getattr(model, n) for n in names) + (
        model.Snapshot.validate, model.Contract.validate, model.State.validate, features)
    return (model.OBJECTS, model.TARGETS, id(BITS), id(STATE_INDEX),
            tuple((f, f.__code__) for f in functions))


def source_fingerprint():
    return sha256(b"".join(Path(p).read_bytes() for p in
        (model.__file__, old.__file__, __file__))).hexdigest()


def features(snapshot):
    """Common checked feature extraction for all compiled controls.

    Unsatisfied at(x,r) implies a future place(x,r). This domain implication is
    available to EVERY compiled arm and does not change the accepted task.
    """
    snapshot.validate()
    mask = 0
    for obligation in snapshot.contract.obligations:
        c = obligation.clause
        if c.kind == "before":
            continue
        if c.kind == "visit" and any(e.seq > obligation.since and e.kind == "place"
            and e.object == c.object and e.target == c.target and e.evidence != "refuted"
            for e in snapshot.history):
            continue
        mask |= BITS[c.kind, c.object or "", c.target or ""]
        if c.kind == "at" and getattr(snapshot.state, c.object) != c.target:
            mask |= BITS["visit", c.object, c.target]
    return mask, STATE_INDEX[snapshot.state.blue, snapshot.state.yellow]


def task_mask(atoms):
    return sum(BITS[a.kind, a.object, a.target] for a in set(atoms))


def canonical(atoms, horizon=8):
    atoms = tuple(a for a in atoms if a.kind != "loc")
    objects = sorted({a.object for a in atoms if a.object})
    targets = sorted({a.target for a in atoms if a.target})
    candidates = []
    for oo in permutations(objects):
        for tt in permutations(targets):
            rename = {**{v: f"?o{i}" for i, v in enumerate(oo)},
                      **{v: f"?t{i}" for i, v in enumerate(tt)}}
            variables = tuple(sorted((v, "object" if v.startswith("?o") else "target") for v in rename.values()))
            candidates.append(Pattern(tuple(sorted(old.instantiate(Pattern(atoms), rename))), variables, horizon))
    return min(candidates, key=lambda p: repr(p.atoms))


def assignments(pattern):
    groups = {}
    for name, sort in pattern.variables:
        if sort not in ("object", "target"):
            raise ValueError("unknown generalized sort")
        groups.setdefault(sort, []).append(name)
    result = [{}]
    for sort, names in sorted(groups.items()):
        values = model.OBJECTS if sort == "object" else model.TARGETS
        result = [{**old_binding, **dict(zip(names, values_permutation))} for old_binding in result
                  for values_permutation in permutations(values, len(names))]
    return result


def minimize(snapshot, oracle, horizon=8):
    """Source-state minimal task core; no test labels and no full-task memo."""
    atoms = set(old.eligible_atoms(snapshot))
    if oracle.solve(atoms, snapshot.state, horizon).status != "conflict":
        return None
    for atom in sorted(atoms):
        if oracle.solve(atoms-{atom}, snapshot.state, horizon).status == "conflict":
            atoms.remove(atom)
    return tuple(sorted(atoms))


def certify_region(pattern, oracle):
    used = {term for a in pattern.atoms for term in (a.object, a.target) if term.startswith("?")}
    if used != {v for v, _ in pattern.variables} or len(used) != len(pattern.variables):
        raise ValueError("variable declaration mismatch")
    if not pattern.atoms or any(a.kind not in ("at", "visit", "forbid", "empty") for a in pattern.atoms):
        raise ValueError("invalid task core")
    if type(pattern.horizon) is not int or not 0 <= pattern.horizon <= 12:
        raise ValueError("invalid horizon")
    certificates = []
    for binding in assignments(pattern):
        atoms = old.instantiate(pattern, binding)
        # Clause validation checks object/target sorts and forbidden extra fields.
        old.contract(atoms)
        bad = 0
        states_proof = []
        for i, state in enumerate(STATES):
            result = oracle.solve(atoms, state, pattern.horizon)
            if result.status == "conflict":
                bad |= 1 << i
            states_proof.append({"state_index": i, "status": result.status,
                                 "plan": [asdict(e) for e in result.plan]})
        certificates.append({"binding": binding, "atoms": [asdict(a) for a in atoms],
            "bad_state_mask": bad, "states": states_proof})
    if not certificates or not any(c["bad_state_mask"] for c in certificates):
        raise ValueError("core has no certified conflict region")
    return {"pattern": asdict(pattern), "key": pattern.key, "bindings": certificates,
        "scope": "maximal_state_guard_for_this_core_in_registered_finite_domain",
        "semantics_sha256": source_fingerprint()}


def expert_patterns():
    """Strong explicit invariant families; use the SAME certification/compiler.

    Protected held-object obstructions are included, strengthening the v1
    handwritten baseline. All context guards come from the same finite oracle.
    """
    families = [
        (Atom("at", "blue", "green"), Atom("at", "yellow", "green")),
        (Atom("at", "blue", "green"), Atom("at", "blue", "return")),
        (Atom("at", "blue", "green"), Atom("forbid", "blue")),
        (Atom("visit", "blue", "green"), Atom("forbid", "blue")),
        (Atom("visit", "yellow", "green"), Atom("forbid", "blue")),
        (Atom("empty"), Atom("forbid", "blue")),
    ]
    return tuple(canonical(atoms) for atoms in families)


class RegionBank:
    """Load-time certificates, precompiled bindings, checked immutable session.

    Model function identities/code and domain constants are checked each query.
    Editing a source file without reloading does not alter executing semantics;
    reloading or monkey-patching an executing function invalidates the session.
    """
    def __init__(self, certificates, horizon=8, *, _token=None):
        certificates = tuple(certificates)
        if _token is not _CERTIFIED:
            # Deserialized tables are untrusted, even if their source hash matches.
            check = old.Oracle()
            for cert in certificates:
                raw = cert["pattern"]
                pattern = Pattern(tuple(Atom(**a) for a in raw["atoms"]),
                                  tuple(tuple(v) for v in raw["variables"]), raw["horizon"])
                if json.dumps(certify_region(pattern, check), sort_keys=True) != json.dumps(cert, sort_keys=True):
                    raise ValueError("untrusted certificate differs from recomputation")
        self.horizon = horizon
        self._token = runtime_token()
        self.certificates = tuple(certificates)
        self.conditions = [[] for _ in STATES]
        self.references = {}
        for cert in certificates:
            if cert["semantics_sha256"] != source_fingerprint():
                raise ValueError("certificate code/domain mismatch")
            if cert["pattern"]["horizon"] != horizon:
                raise ValueError("certificate horizon mismatch")
            for entry in cert["bindings"]:
                atoms = tuple(Atom(**a) for a in entry["atoms"])
                bits = task_mask(atoms)
                for i in range(len(STATES)):
                    if entry["bad_state_mask"] & (1 << i):
                        self.conditions[i].append(bits)
                        self.references[i, bits] = {"key": cert["key"], "binding": entry["binding"]}
        # Sound subsumption removes redundant conjunctions, not any test labels.
        for i, entries in enumerate(self.conditions):
            reduced = []
            for bits in sorted(set(entries), key=lambda x: (x.bit_count(), x)):
                if not any(existing & bits == existing for existing in reduced):
                    reduced.append(bits)
            self.conditions[i] = tuple(reduced)

    @classmethod
    def verified(cls, patterns, oracle, horizon=8):
        certs = [certify_region(p, oracle) for p in {p.key: p for p in patterns}.values()]
        return cls(certs, horizon, _token=_CERTIFIED)

    @classmethod
    def combine(cls, *banks):
        if not banks or any(b._token != runtime_token() for b in banks):
            raise ValueError("invalid or stale source bank")
        if len({b.horizon for b in banks}) != 1:
            raise ValueError("mixed horizons")
        certs = {c["key"]: c for b in banks for c in b.certificates}
        return cls(tuple(certs.values()), banks[0].horizon, _token=_CERTIFIED)

    def match(self, snapshot, horizon=8):
        if self._token != runtime_token():
            raise ValueError("executing domain changed: recertify")
        if type(horizon) is not int or horizon < 0 or horizon > self.horizon:
            return False
        available, state_index = features(snapshot)
        return any(required & available == required for required in self.conditions[state_index])

    def evidence(self, snapshot):
        if self._token != runtime_token():
            raise ValueError("executing domain changed: recertify")
        available, state_index = features(snapshot)
        for required in self.conditions[state_index]:
            if required & available == required:
                return {**self.references[state_index, required], "state_index": state_index,
                    "support": [asdict(a) for i, a in enumerate(POOL) if required & (1 << i)]}
        return None
