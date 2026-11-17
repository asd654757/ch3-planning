"""Proposal-only, exact minimum-deletion repair from frozen conflict knowledge.

Implicit hitting sets and specification relaxation are existing techniques.
The candidate mechanism preserves the supports connecting necessary atoms to
editable obligations. It is deliberately compared to candidate-wise rechecking.
No method applies a contract, erases evidence, or grants authority to execute.
"""
from dataclasses import dataclass, replace
from itertools import combinations


METHODS = ("enumerate", "online_core", "transfer_union", "transfer_support", "transfer_recheck")


@dataclass(frozen=True)
class RepairProposal:
    status: str
    deleted: tuple[str, ...]
    cost: int | None
    contract: object | None
    plan: tuple
    stats: dict


def editable_ids(snapshot):
    """Pilot permission fixture, not a policy inferred from natural language."""
    ordered = sorted(snapshot.contract.obligations, key=lambda o: o.clause.id)
    goals = [o.clause.id for o in ordered if o.clause.kind == "at"]
    protected = set(goals[:1])
    return tuple(o.clause.id for o in ordered
                 if o.clause.kind not in ("forbid", "before") and o.clause.id not in protected)


def retained_contract(domain, snapshot, deleted):
    """Existing immutable obligations retain IDs, content and effective times."""
    return domain.model.Contract(snapshot.contract.version + 1, tuple(
        o for o in snapshot.contract.obligations if o.clause.id not in deleted)).validate()


def candidates(domain, snapshot, editable, costs):
    result = []
    for size in range(len(editable) + 1):
        for ids in combinations(sorted(editable), size):
            try:
                task = retained_contract(domain, snapshot, ids)
            except ValueError:  # Removing a referenced visit may leave a dangling order.
                continue
            result.append((sum(costs[i] for i in ids), ids, task))
    return sorted(result, key=lambda row: row[:2])


class SupportConstraints:
    """Exact deletion preimage of a bank's currently applicable static cores.

    features is a union of independent obligation supports in this domain.
    Destroys(core) iff at least one atom has all its supports deleted. This
    equivalence is restricted to deletion, fixed state/history and current H.
    """
    def __init__(self, domain, bank, snapshot):
        if bank._token != domain.regions.runtime_token():
            raise ValueError("executing model changed")
        self.domain, self.bank, self.snapshot = domain, bank, snapshot
        available, index = domain.features(snapshot)
        support = {}
        for obligation in snapshot.contract.obligations:
            if obligation.clause.kind == "before":
                continue
            task = domain.model.Contract(snapshot.contract.version, (obligation,))
            bits, _ = domain.features(replace(snapshot, contract=task))
            while bits:
                bit = bits & -bits
                support.setdefault(bit, set()).add(obligation.clause.id)
                bits ^= bit
        self.groups = []
        for required in bank.conditions[index]:
            if required & available != required:
                continue
            groups, bits = [], required
            while bits:
                bit = bits & -bits
                if bit not in support:
                    raise AssertionError("unexplained necessary feature")
                groups.append(frozenset(support[bit]))
                bits ^= bit
            self.groups.append(tuple(groups))

    def blocked(self, deleted, *, union=False):
        if self.bank._token != self.domain.regions.runtime_token():
            raise ValueError("executing model changed")
        deleted = frozenset(deleted)
        if union:
            return any(not deleted.intersection(set().union(*groups)) for groups in self.groups)
        return any(all(not supporters <= deleted for supporters in groups) for groups in self.groups)


def propose(domain, snapshot, bank, lower, method, *, editable=None, costs=None,
            horizon=8, known_nonready=False):
    snapshot.validate()
    if method not in METHODS:
        raise ValueError("unknown repair method")
    if horizon != bank.horizon:
        raise ValueError("certificate horizon mismatch")
    editable = tuple(editable_ids(snapshot) if editable is None else editable)
    all_ids = {o.clause.id for o in snapshot.contract.obligations}
    if len(set(editable)) != len(editable) or not set(editable) <= all_ids:
        raise ValueError("invalid editable obligations")
    if len(editable) > 20:
        raise ValueError("pilot supports at most twenty editable obligations")
    costs = {i: 1 for i in editable} if costs is None else costs
    if set(costs) != set(editable) or any(type(v) is not int or v <= 0 for v in costs.values()):
        raise ValueError("edit costs must be positive integers")
    rows = candidates(domain, snapshot, editable, costs)
    lookup = {ids: task for _, ids, task in rows}
    constraints = SupportConstraints(domain, bank, snapshot) if method in (
        "transfer_union", "transfer_support") else None
    stats = {k: 0 for k in ("backend_calls", "lower_bound_rejections", "certificate_rejections",
        "online_core_rejections", "cache_hits", "candidates_considered", "shrink_queries")}
    stats["candidate_space"] = len(rows)
    stats["transferred_conditions"] = len(constraints.groups) if constraints else 0
    memo, cores = {}, []
    # The caller must possess a non-ready decision for THIS immutable snapshot.
    if known_nonready:
        memo[()] = (False, ())

    def query(ids):
        if ids in memo:
            stats["cache_hits"] += 1
            return memo[ids]
        task = lookup[ids]
        current = replace(snapshot, contract=task)
        if lower.match(current, horizon):
            stats["lower_bound_rejections"] += 1
            answer = False, ()
        else:
            stats["backend_calls"] += 1
            decision = domain.model.decide(current, task, horizon)
            answer = decision.status == "ready", decision.plan
        memo[ids] = answer
        return answer

    for cost, ids, task in rows:
        stats["candidates_considered"] += 1
        removed = frozenset(ids)
        if any(not core.intersection(removed) for core in cores):
            stats["online_core_rejections"] += 1
            continue
        if constraints is not None and constraints.blocked(ids, union=method == "transfer_union"):
            stats["certificate_rejections"] += 1
            continue
        if method == "transfer_recheck" and bank.evidence(replace(snapshot, contract=task)) is not None:
            stats["certificate_rejections"] += 1
            continue
        ready, plan = query(ids)
        if ready:
            return RepairProposal("proposal", ids, cost, task, tuple(plan), dict(stats))
        if method == "enumerate":
            continue
        # Ordinary deletion-based online core shrink, including all actual costs.
        # Hard obligations remain present. In this valid-contract language,
        # before references may make some trial subsets inadmissible.
        dropped = set(ids)
        for item in editable:
            trial = tuple(sorted(dropped | {item}))
            if item in dropped or trial not in lookup:
                continue
            stats["shrink_queries"] += 1
            if not query(trial)[0]:
                dropped.add(item)
        cores.append(frozenset(set(editable) - dropped))
    return RepairProposal("no_authorized_repair", (), None, None, (), dict(stats))
