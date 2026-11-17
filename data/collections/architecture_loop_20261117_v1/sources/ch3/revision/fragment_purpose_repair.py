"""Proposal-only grouped target substitution under an explicit snapshot policy.

Equivalence is declared by the task configuration, never learned from failure.
The complete registered goals are retained. Both old and newly introduced
nogoods are checked. This is a small finite-domain specification-repair module,
not a claim of new general CSP/optimal-relaxation algorithms.
"""
from dataclasses import dataclass, replace
from itertools import product


@dataclass(frozen=True)
class PurposePolicy:
    id: str
    snapshot_digest: str
    swappable_objects: tuple[str, ...]
    equivalent_targets: tuple[str, str] = ("green", "return")

    def validate(self, domain, snapshot):
        snapshot.validate()
        if not self.id or self.snapshot_digest != snapshot.digest:
            raise ValueError("missing or stale purpose policy")
        if (not isinstance(self.swappable_objects, tuple)
                or len(set(self.swappable_objects)) != len(self.swappable_objects)
                or not set(self.swappable_objects) <= set(domain.model.OBJECTS)):
            raise ValueError("invalid object permission")
        if (not isinstance(self.equivalent_targets, tuple) or len(self.equivalent_targets) != 2
                or len(set(self.equivalent_targets)) != 2 or "table" in self.equivalent_targets
                or not set(self.equivalent_targets) <= set(domain.model.TARGETS)):
            raise ValueError("only two explicitly equivalent registered buffers are supported")
        return self


def locked_objects(snapshot, targets):
    return frozenset(o.clause.object for o in snapshot.contract.obligations
        if o.clause.kind == "visit" and o.clause.target in targets and any(
            e.seq > o.since and e.kind == "place" and e.object == o.clause.object
            and e.target == o.clause.target and e.evidence != "refuted" for e in snapshot.history))


def variables(domain, snapshot, policy):
    policy.validate(domain, snapshot)
    present = {o.clause.object for o in snapshot.contract.obligations
               if o.clause.kind in ("at", "visit") and o.clause.target in policy.equivalent_targets}
    return tuple(sorted((set(policy.swappable_objects) & present)
                        - locked_objects(snapshot, policy.equivalent_targets)))


def swapped_obligation(domain, snapshot, obligation, swapped, targets):
    c = obligation.clause
    if c.object not in swapped or c.kind not in ("at", "visit") or c.target not in targets:
        return obligation
    replacement = c.model_copy(update={"target": targets[1] if c.target == targets[0] else targets[0]})
    return domain.model.Obligation(replacement, snapshot.cursor)


@dataclass(frozen=True)
class TargetEdit:
    id: str
    previous_target: str
    target: str


@dataclass(frozen=True)
class Candidate:
    cost: int
    edits: tuple[TargetEdit, ...]
    swapped_objects: tuple[str, ...]
    contract: object


def candidates(domain, snapshot, policy):
    names = variables(domain, snapshot, policy)
    result = []
    for bits in product((False, True), repeat=len(names)):
        swapped = tuple(o for o, bit in zip(names, bits) if bit)
        obligations = tuple(swapped_obligation(domain, snapshot, o, swapped, policy.equivalent_targets)
                            for o in snapshot.contract.obligations)
        edits = tuple(sorted((TargetEdit(old.clause.id, old.clause.target, new.clause.target)
            for old, new in zip(snapshot.contract.obligations, obligations) if old.clause != new.clause),
            key=lambda e: e.id))
        task = domain.model.Contract(snapshot.contract.version + 1, obligations).validate()
        result.append(Candidate(len(edits), edits, swapped, task))
    return tuple(sorted(result, key=lambda c: (c.cost, tuple((e.id, e.target) for e in c.edits))))


def admissible(domain, snapshot, policy, contract):
    """Independent check of the policy relation; no deletion or anchor reset."""
    policy.validate(domain, snapshot)
    contract.validate()
    if contract.version != snapshot.contract.version + 1:
        return False
    original = snapshot.contract.obligations
    if tuple(o.clause.id for o in original) != tuple(o.clause.id for o in contract.obligations):
        return False
    allowed = set(variables(domain, snapshot, policy))
    renamed = set()
    for old, new in zip(original, contract.obligations):
        if old == new:
            continue
        a, b = old.clause, new.clause
        if (a.object not in allowed or a.kind not in ("at", "visit")
                or a.target not in policy.equivalent_targets or new.since != snapshot.cursor):
            return False
        expected = a.model_copy(update={"target": next(t for t in policy.equivalent_targets if t != a.target)})
        if b != expected:
            return False
        renamed.add(a.object)
    for old, new in zip(original, contract.obligations):
        a = old.clause
        if a.object in renamed and a.kind in ("at", "visit") and a.target in policy.equivalent_targets:
            if a.target == new.clause.target:
                return False
    return True


def valid_plan(domain, snapshot, plan, horizon):
    if len(plan) > horizon:
        return False
    state = snapshot.state
    for i, event in enumerate(plan, 1):
        if event.seq != snapshot.cursor + i:
            return False
        state = next((ns for ns, e in domain.model.successors(state, event.seq)
            if (e.kind, e.object, e.target) == (event.kind, event.object, event.target)), None)
        if state is None:
            return False
    return all(domain.model.monitor(snapshot.contract, state, (*h, *plan)) == (frozenset(), frozenset())
               for h in domain.model.possible_histories(snapshot.history))


class SharedReadyCache:
    """Common source witnesses. A cache entry alone grants no ready decision."""
    def __init__(self, domain, rows=()):
        self.domain = domain
        self.token = domain.regions.runtime_token()
        self.key = domain.Session.resolve.__globals__["exact_key"]
        self.entries = {tuple(r["key"]): tuple(domain.model.Event(**e) for e in r["plan"])
                        for r in rows if r["status"] == "ready"}

    def query(self, snapshot, horizon):
        if self.token != self.domain.regions.runtime_token():
            raise ValueError("executing domain changed")
        key = self.key(snapshot)
        if key is None or key not in self.entries:
            return "miss", ()
        plan = tuple(replace(e, seq=snapshot.cursor+i, observation_id="prediction-only")
                     for i, e in enumerate(self.entries[key], 1))
        return ("hit", plan) if valid_plan(self.domain, snapshot, plan, horizon) else ("invalid", ())


class OptionFeatures:
    """Union of per-group option features, including newly introduced cores."""
    def __init__(self, domain, bank, snapshot, policy):
        self.domain, self.bank = domain, bank
        self.token = domain.regions.runtime_token()
        if self.token != bank._token:
            raise ValueError("stale or foreign certificate bank")
        names = variables(domain, snapshot, policy)
        self.base = 0
        self.options = {name: [0, 0] for name in names}
        _, self.state_index = domain.features(snapshot)
        for obligation in snapshot.contract.obligations:
            if obligation.clause.kind == "before":
                continue
            name = obligation.clause.object
            choices = (False, True) if name in self.options else (False,)
            for swapped in choices:
                item = swapped_obligation(domain, snapshot, obligation, (name,) if swapped else (),
                                          policy.equivalent_targets)
                task = domain.model.Contract(snapshot.contract.version + 1, (item,))
                bits, _ = domain.features(replace(snapshot, contract=task))
                if name in self.options:
                    self.options[name][int(swapped)] |= bits
                else:
                    self.base |= bits

    def feature_mask(self, swapped):
        if self.token != self.domain.regions.runtime_token():
            raise ValueError("executing domain changed")
        if not set(swapped) <= set(self.options):
            raise ValueError("choice outside compiled policy")
        bits = self.base
        for name, options in self.options.items():
            bits |= options[int(name in swapped)]
        return bits

    def blocked(self, swapped):
        available = self.feature_mask(swapped)
        return any(required & available == required for required in self.bank.conditions[self.state_index])


@dataclass(frozen=True)
class PurposeProposal:
    status: str
    snapshot_digest: str
    policy_id: str
    cost: int | None
    edits: tuple[TargetEdit, ...]
    swapped_objects: tuple[str, ...]
    contract: object | None
    plan: tuple
    stats: dict


def propose(domain, snapshot, policy, bank, lower, cache, *, method="recheck", horizon=8, known_nonready=False):
    if method not in ("standard", "recheck", "compiled") or bank.horizon != horizon:
        raise ValueError("unknown method or mismatched horizon")
    if (lower.domain is not domain or cache.domain is not domain
            or bank._token != domain.regions.runtime_token()):
        raise ValueError("foreign or stale repair components")
    rows = candidates(domain, snapshot, policy)
    compiled = OptionFeatures(domain, bank, snapshot, policy) if method == "compiled" else None
    stats = {key: 0 for key in ("backend_calls", "known_nonready_reuses", "certificate_rejections",
        "lower_bound_rejections", "ready_cache_hits", "invalid_ready_cache", "candidates_considered")}
    stats["candidate_space"] = len(rows)
    stats["variable_groups"] = len(variables(domain, snapshot, policy))
    for row in rows:
        stats["candidates_considered"] += 1
        if not row.edits and known_nonready:
            stats["known_nonready_reuses"] += 1
            continue
        current = replace(snapshot, contract=row.contract)
        if method == "compiled" and compiled.blocked(row.swapped_objects):
            stats["certificate_rejections"] += 1
            continue
        if method == "recheck" and bank.evidence(current) is not None:
            stats["certificate_rejections"] += 1
            continue
        if lower.match(current, horizon):
            stats["lower_bound_rejections"] += 1
            continue
        cached, plan = cache.query(current, horizon)
        stats["invalid_ready_cache"] += cached == "invalid"
        if cached == "hit":
            stats["ready_cache_hits"] += 1
        else:
            stats["backend_calls"] += 1
            decision = domain.model.decide(current, row.contract, horizon)
            if decision.status != "ready":
                continue
            plan = decision.plan
        return PurposeProposal("proposal", snapshot.digest, policy.id, row.cost, row.edits,
                               row.swapped_objects, row.contract, tuple(plan), dict(stats))
    return PurposeProposal("no_permitted_substitution", snapshot.digest, policy.id, None, (), (),
                           None, (), dict(stats))
