"""Mechanical object-count adapter for the frozen finite-domain algorithms.

Original search/core/compiler/session functions keep their code objects. Only
domain constants, state validation/enumeration/indexing, and certificate domain
fingerprints change. No global in the original modules is mutated.
"""
from dataclasses import make_dataclass
from hashlib import sha256
import importlib.util
from itertools import count, product
from pathlib import Path
import sys
from types import FunctionType, MappingProxyType, SimpleNamespace

from ch3.revision import history, fragments, fragment_regions, fragment_session

_SERIAL = count()


def _function(function, namespace):
    rebound = FunctionType(function.__code__, namespace, function.__name__,
                           function.__defaults__, function.__closure__)
    rebound.__kwdefaults__ = function.__kwdefaults__
    rebound.__annotations__ = function.__annotations__
    return rebound


def _functions(module, namespace):
    for name, value in vars(module).items():
        if isinstance(value, FunctionType) and value.__module__ == module.__name__:
            namespace[name] = _function(value, namespace)


def _class(cls, namespace):
    attributes = {}
    for name, value in vars(cls).items():
        if name in ("__dict__", "__weakref__"):
            continue
        if isinstance(value, FunctionType):
            value = _function(value, namespace)
        elif isinstance(value, classmethod):
            value = classmethod(_function(value.__func__, namespace))
        elif isinstance(value, staticmethod):
            value = staticmethod(_function(value.__func__, namespace))
        attributes[name] = value
    return type(cls.__name__, cls.__bases__, attributes)


def build_domain(objects=("blue", "yellow", "red")):
    objects = tuple(objects)
    if not 2 <= len(objects) <= 4 or len(set(objects)) != len(objects):
        raise ValueError("adapter supports two to four distinct objects")
    if objects[:2] != ("blue", "yellow") or any(not o.isidentifier() or o == "holding" for o in objects):
        raise ValueError("registered object names must begin with blue/yellow")
    name = f"ch3.revision._finite_history_{next(_SERIAL)}"
    spec = importlib.util.spec_from_file_location(name, history.__file__)
    model = importlib.util.module_from_spec(spec)
    sys.modules[name] = model
    spec.loader.exec_module(model)
    model.OBJECTS = objects

    def validate(state):
        if state.holding is not None and state.holding not in objects:
            raise ValueError("unknown held object")
        for obj in objects:
            location = getattr(state, obj)
            if location not in (*model.TARGETS, "hand"):
                raise ValueError("unknown location")
            if (location == "hand") != (state.holding == obj):
                raise ValueError("holding/location disagreement")
        for target in model.TARGETS:
            if target != "table" and sum(getattr(state, o) == target for o in objects) > 1:
                raise ValueError("region capacity exceeded")
        return state

    model.State = make_dataclass("FiniteState", [(o, str, "table") for o in objects]
                                + [("holding", str | None, None)], frozen=True,
                                namespace={"validate": validate})

    def state_key(state):
        return tuple(getattr(state, o) for o in objects)

    legal_states = []
    for locations in product((*model.TARGETS, "hand"), repeat=len(objects)):
        held = [o for o, location in zip(objects, locations) if location == "hand"]
        if len(held) > 1:
            continue
        state = model.State(**dict(zip(objects, locations)), holding=held[0] if held else None)
        try:
            legal_states.append(state.validate())
        except ValueError:
            pass
    legal_states = tuple(legal_states)

    old = dict(vars(fragments))
    old.update(semantics=model, Clause=model.Clause, Contract=model.Contract,
               Obligation=model.Obligation, Snapshot=model.Snapshot, State=model.State,
               decide=model.decide)
    _functions(fragments, old)
    old["states"] = lambda: legal_states
    old["Oracle"] = _class(fragments.Oracle, old)
    old_namespace = SimpleNamespace(**old)

    region = dict(vars(fragment_regions))
    region.update(model=model, old=old_namespace, STATES=legal_states)
    region["STATE_INDEX"] = MappingProxyType({state_key(s): i for i, s in enumerate(legal_states)})
    pool = tuple(fragments.Atom(k, o, t) for k in ("at", "visit")
                 for o in objects for t in model.TARGETS)
    pool += tuple(fragments.Atom("forbid", o) for o in objects) + (fragments.Atom("empty"),)
    region["POOL"] = pool
    region["BITS"] = MappingProxyType({(a.kind, a.object, a.target): 1 << i for i, a in enumerate(pool)})
    _functions(fragment_regions, region)

    def domain_fingerprint():
        files = (history.__file__, fragments.__file__, fragment_regions.__file__,
                 fragment_session.__file__, __file__)
        payload = b"".join(Path(p).read_bytes() for p in files)
        return sha256(payload + repr((objects, model.TARGETS)).encode()).hexdigest()

    def features(snapshot):
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
            mask |= region["BITS"][c.kind, c.object or "", c.target or ""]
            if c.kind == "at" and getattr(snapshot.state, c.object) != c.target:
                mask |= region["BITS"]["visit", c.object, c.target]
        return mask, region["STATE_INDEX"][state_key(snapshot.state)]

    region["source_fingerprint"] = domain_fingerprint
    region["features"] = features
    region["RegionBank"] = _class(fragment_regions.RegionBank, region)

    session = dict(vars(fragment_session))
    session.update(RegionBank=region["RegionBank"], Oracle=old["Oracle"],
                   canonical=region["canonical"], features=features, minimize=region["minimize"],
                   Event=model.Event, Snapshot=model.Snapshot, decide=model.decide)
    _functions(fragment_session, session)
    session["FragmentSession"] = _class(fragment_session.FragmentSession, session)

    unchanged = {}
    for function in ("decide", "_search", "monitor", "successors", "possible_histories"):
        unchanged["history." + function] = getattr(model, function).__code__.co_code == getattr(history, function).__code__.co_code
    for function in ("canonical", "minimize", "certify_region", "assignments", "task_mask"):
        unchanged["regions." + function] = region[function].__code__ is getattr(fragment_regions, function).__code__
    for method in ("__init__", "match", "combine", "verified", "evidence"):
        unchanged["bank." + method] = getattr(region["RegionBank"], method).__code__ is getattr(fragment_regions.RegionBank, method).__code__
    unchanged["session.resolve"] = session["FragmentSession"].resolve.__code__ is fragment_session.FragmentSession.resolve.__code__
    assert all(unchanged.values())
    return SimpleNamespace(model=model, old=old_namespace, regions=SimpleNamespace(**region),
                           Session=session["FragmentSession"], states=legal_states,
                           state_key=state_key, features=features, fingerprint=domain_fingerprint,
                           unchanged_algorithms=unchanged)


class IndependentActionBound:
    """Admissible baseline: sum exact per-object costs ignoring other objects.

    The common precomputed table relaxes hand/resource interference, so cannot
    reject a feasible bounded task. This baseline is available to both arms.
    """
    def __init__(self, domain):
        self.domain = domain
        self.targets = domain.model.TARGETS
        self.table = {}
        from collections import deque
        for current, goal, visits, protected, empty in product(
                (*self.targets, "hand"), (None, *self.targets), range(1 << len(self.targets)),
                (False, True), (False, True)):
            queue = deque([(current, 0, 0)])
            seen = set()
            distance = 99
            while queue:
                location, completed, steps = queue.popleft()
                if (location, completed) in seen:
                    continue
                seen.add((location, completed))
                if (goal is None or location == goal) and completed & visits == visits and (not empty or location != "hand"):
                    distance = steps
                    break
                if protected:
                    continue
                if location == "hand":
                    for i, target in enumerate(self.targets):
                        queue.append((target, completed | (1 << i), steps + 1))
                else:
                    queue.append(("hand", completed, steps + 1))
            self.table[current, goal, visits, protected, empty] = distance

    def value(self, snapshot):
        atoms = self.domain.old.eligible_atoms(snapshot)
        empty = any(a.kind == "empty" for a in atoms)
        total = 0
        for obj in self.domain.model.OBJECTS:
            goals = {a.target for a in atoms if a.kind == "at" and a.object == obj}
            if len(goals) > 1:
                return 99
            visits = sum(1 << i for i, t in enumerate(self.targets)
                         if fragments.Atom("visit", obj, t) in atoms)
            protected = fragments.Atom("forbid", obj) in atoms
            total += self.table[getattr(snapshot.state, obj), next(iter(goals), None), visits, protected, empty]
        return total

    def match(self, snapshot, horizon=8):
        return self.value(snapshot) > horizon
