"""Failure-derived, deletion-minimal bounded conflict fragments.

This is symbolic core extraction and verified nogood reuse, not model training
or discovery of unknown physics. Proofs cover ONLY the existing two-object
domain, unit-capacity regions, one hand and the certified action horizon.
Historical order is deliberately relaxed; absence of a match means abstain.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import permutations, product
import json
from pathlib import Path
from time import perf_counter

from ch3.revision import history as semantics
from ch3.revision.history import Clause, Contract, Obligation, Snapshot, State, decide


@dataclass(frozen=True, order=True)
class Atom:
    kind: str
    object: str = ""
    target: str = ""


@dataclass(frozen=True)
class Pattern:
    atoms: tuple[Atom, ...]
    variables: tuple[tuple[str, str], ...] = ()
    horizon: int = 8

    @property
    def key(self):
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def semantics_fingerprint():
    # Re-certification is mandatory after either inference code or domain changes.
    files = (Path(semantics.__file__), Path(__file__))
    return sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def states():
    result = []
    for blue, yellow in product((*semantics.TARGETS, "hand"), repeat=2):
        held = [obj for obj, loc in (("blue", blue), ("yellow", yellow)) if loc == "hand"]
        if len(held) > 1:
            continue
        try:
            result.append(State(blue, yellow, held[0] if held else None).validate())
        except ValueError:
            pass
    return tuple(result)


def eligible_atoms(snapshot):
    """A necessary future-task relaxation, never a stronger replacement task.

    A visit becomes a future obligation only if NO confirmed/unknown eligible
    placement can already fulfill it. Forbids retain only their future part.
    Order and historical violations are discarded. Thus an infeasible relaxed
    task implies infeasibility of the actual task; the reverse need not hold.
    """
    snapshot.validate()
    result = set()
    for o in snapshot.contract.obligations:
        c = o.clause
        if c.kind == "before":
            continue
        if c.kind == "visit" and any(e.seq > o.since and e.kind == "place"
            and e.object == c.object and e.target == c.target and e.evidence != "refuted"
            for e in snapshot.history):
            continue
        result.add(Atom(c.kind, c.object or "", c.target or ""))
    return frozenset(result)


def location_atoms(state):
    return frozenset(Atom("loc", obj, getattr(state, obj)) for obj in semantics.OBJECTS)


def contract(atoms):
    return Contract(0, tuple(Obligation(Clause(id=f"c{i}", kind=a.kind,
        **({"object": a.object} if a.object else {}),
        **({"target": a.target} if a.target else {})))
        for i, a in enumerate(sorted(atoms)) if a.kind != "loc")).validate()


class Oracle:
    def __init__(self):
        self.calls = 0
        self.elapsed_ms = 0.0

    def solve(self, atoms, state, horizon):
        task = contract(atoms)
        snapshot = Snapshot(task, state, (), "fragment-proof").validate()
        start = perf_counter()
        self.calls += 1
        try:
            return decide(snapshot, task, horizon)
        finally:
            self.elapsed_ms += (perf_counter()-start)*1000


def refutes(atoms, horizon, oracle):
    """Universal finite check over ALL valid states agreeing with kept facts."""
    guards = {a for a in atoms if a.kind == "loc"}
    checked = 0
    for state in states():
        if guards <= location_atoms(state):
            checked += 1
            decision = oracle.solve(atoms, state, horizon)
            if decision.status != "conflict":
                return False, {"state": asdict(state), "plan": [asdict(e) for e in decision.plan]}
    if not checked:
        raise ValueError("vacuous or inconsistent state guard")
    return True, {"checked_states": checked}


def extract(snapshot, oracle, horizon=8, reverse=False):
    """Greedy deletion finds one inclusion-minimal core, not the smallest core.

    A rejected candidate is only the trigger. Promotion requires an independent
    universal infeasibility check of the relaxed task, with no gold labels.
    """
    atoms = set(eligible_atoms(snapshot) | location_atoms(snapshot.state))
    bad, _ = refutes(atoms, horizon, oracle)
    if not bad:
        return None
    order = sorted(atoms, key=lambda a: (a.kind != "loc", a.kind, a.object, a.target), reverse=reverse)
    for atom in order:
        trial = atoms-{atom}
        bad, _ = refutes(trial, horizon, oracle)
        if bad:
            atoms = trial
    return Pattern(tuple(sorted(atoms)), horizon=horizon)


def instantiate(pattern, binding):
    return tuple(Atom(a.kind, binding.get(a.object, a.object), binding.get(a.target, a.target))
                 for a in pattern.atoms)


def bindings(pattern):
    if len(dict(pattern.variables)) != len(pattern.variables):
        raise ValueError("duplicate variables")
    groups = {}
    for variable, sort in pattern.variables:
        if not variable.startswith("?") or sort not in ("object", "unit_region"):
            raise ValueError("unregistered variable/sort")
        groups.setdefault(sort, []).append(variable)
    options = [{}]
    for sort, names in sorted(groups.items()):
        values = semantics.OBJECTS if sort == "object" else ("green", "return")
        options = [{**old, **dict(zip(names, assignment))} for old in options
                   for assignment in permutations(values, len(names))]
    if not options:
        raise ValueError("no injective bindings in certified domain")
    return tuple(options)


def lift(pattern):
    """Canonical alpha-renaming; table/hand retain their distinct domain roles."""
    objects = sorted({a.object for a in pattern.atoms if a.object})
    regions = sorted({a.target for a in pattern.atoms if a.target in ("green", "return")})
    candidates = []
    for obj_order in permutations(objects):
        for region_order in permutations(regions):
            rename = {**{obj: f"?o{i}" for i, obj in enumerate(obj_order)},
                      **{region: f"?r{i}" for i, region in enumerate(region_order)}}
            atoms = tuple(sorted(instantiate(pattern, rename)))
            variables = tuple(sorted((name, "object" if name.startswith("?o") else "unit_region")
                                     for name in rename.values()))
            candidates.append(Pattern(atoms, variables, pattern.horizon))
    return min(candidates, key=lambda p: repr(p.atoms))


def certify(pattern, oracle):
    if type(pattern.horizon) is not int or not 0 <= pattern.horizon <= 12:
        raise ValueError("uncertified action horizon")
    if not pattern.atoms or len(set(pattern.atoms)) != len(pattern.atoms):
        raise ValueError("empty/duplicate fragment")
    used = {term for a in pattern.atoms for term in (a.object, a.target) if term.startswith("?")}
    if used != {name for name, _ in pattern.variables}:
        raise ValueError("variable declaration mismatch")
    for a in pattern.atoms:
        if a.kind not in ("loc", "at", "visit", "forbid", "empty"):
            raise ValueError("unsupported fragment kind")
        sorts = dict(pattern.variables)
        if a.object.startswith("?") and sorts[a.object] != "object":
            raise ValueError("object variable has wrong sort")
        if a.target.startswith("?") and sorts[a.target] != "unit_region":
            raise ValueError("region variable has wrong sort")
    checks, witnesses = [], []
    before = oracle.calls
    for index, binding in enumerate(bindings(pattern)):
        atoms = instantiate(pattern, binding)
        bad, details = refutes(atoms, pattern.horizon, oracle)
        if not bad:
            raise ValueError(f"fragment counterexample: {details}")
        checks.append({"binding": binding, **details})
        if index == 0:
            for atom in atoms:
                still_bad, witness = refutes(set(atoms)-{atom}, pattern.horizon, oracle)
                if still_bad:
                    raise ValueError("fragment is not deletion-minimal")
                witnesses.append({"removed_atom": asdict(atom), **witness})
    return {"pattern_sha256": pattern.key, "semantics_sha256": semantics_fingerprint(),
        "scope": "existing_two_object_one_hand_unit_capacity_domain_only",
        "horizon": pattern.horizon, "all_bindings": checks,
        "deletion_witnesses_first_binding": witnesses,
        "oracle_calls": oracle.calls-before, "inclusion_minimal": True}


class VerifiedBank:
    """Untrusted patterns are certified on load; matching alone never calls BFS."""
    def __init__(self, patterns, oracle):
        self.patterns = tuple({p.key: p for p in patterns}.values())
        self.certificates = tuple(certify(p, oracle) for p in self.patterns)
        self.fingerprint = semantics_fingerprint()

    def match(self, snapshot, horizon=8):
        snapshot.validate()
        if type(horizon) is not int or not 0 <= horizon <= 12:
            raise ValueError("horizon outside bounded domain")
        if self.fingerprint != semantics_fingerprint():
            raise ValueError("domain/inference changed: re-certification required")
        available = eligible_atoms(snapshot) | location_atoms(snapshot.state)
        for pattern in self.patterns:
            if horizon > pattern.horizon:
                continue
            for binding in bindings(pattern):
                if set(instantiate(pattern, binding)) <= available:
                    return {"status": "conflict", "pattern": pattern.key,
                        "binding": binding, "horizon": horizon,
                        "support": [asdict(a) for a in instantiate(pattern, binding)]}
        return None


def structural_conflict(snapshot):
    """Strong handwritten invariant baseline; no learned patterns or BFS.

    This is intentionally capable of recovering the simple known-domain rules.
    A learned cache must not claim exclusive ability to detect these conflicts.
    """
    atoms = eligible_atoms(snapshot)
    protected = {a.object for a in atoms if a.kind == "forbid"}
    goals = [a for a in atoms if a.kind == "at"]
    for a in goals:
        if any(b.object == a.object and b.target != a.target for b in goals):
            return True
        if a.target != "table" and any(b.object != a.object and b.target == a.target for b in goals):
            return True
        if a.object in protected and getattr(snapshot.state, a.object) != a.target:
            return True
    for a in atoms:
        if a.kind == "empty" and snapshot.state.holding in protected:
            return True
        if a.kind == "visit" and a.object in protected:
            return True
        if a.kind in ("at", "visit") and a.target != "table" and any(
            obj != a.object and obj in protected and getattr(snapshot.state, obj) == a.target
            for obj in semantics.OBJECTS):
            return True
    return False
