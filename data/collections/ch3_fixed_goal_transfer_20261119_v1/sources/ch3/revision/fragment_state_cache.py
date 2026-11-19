"""Strong state-bound nogood control with checked physical symmetries.

Whole source states are preserved. Object names and unit-capacity regions may
be permuted; table and hand retain their types. Every resulting point is checked
again. This control shares source cores, feature closure and the batch engine
with the full state-region method, but does not enumerate other state guards.
"""
from dataclasses import asdict
from itertools import permutations

from ch3.revision.fragments import Atom


class StateBoundBank:
    def __init__(self, domain, fixed_bank, failures, oracle=None):
        self.domain = domain
        self.fixed = fixed_bank
        self.horizon = fixed_bank.horizon
        self._token = domain.regions.runtime_token()
        if fixed_bank._token != self._token:
            raise ValueError("fixed bank belongs to another or stale domain")
        oracle = oracle or domain.old.Oracle()
        self.conditions = [[] for _ in domain.states]
        self.entries = {}
        for failure in failures:
            if failure["semantics_sha256"] != domain.fingerprint() or failure["horizon"] != self.horizon:
                raise ValueError("state cache domain or horizon mismatch")
            atoms = tuple(Atom(**a) for a in failure["core"])
            if not atoms or any(a.kind not in ("at", "visit", "forbid", "empty") for a in atoms):
                raise ValueError("unsupported source core")
            domain.old.contract(atoms)
            state = domain.model.State(**failure["state"]).validate()
            key = domain.regions.canonical(atoms, self.horizon).key
            if key != failure["key"]:
                raise ValueError("source pattern key mismatch")
            for names in permutations(domain.model.OBJECTS):
                rename = dict(zip(domain.model.OBJECTS, names))
                for regions in (("green", "return"), ("return", "green")):
                    places = {"table": "table", "hand": "hand", **dict(zip(("green", "return"), regions))}
                    mapped_atoms = tuple(sorted(Atom(a.kind, rename.get(a.object, ""), places.get(a.target, "")) for a in atoms))
                    mapped_state = domain.model.State(**{rename[o]: places[getattr(state, o)] for o in domain.model.OBJECTS},
                        holding=rename.get(state.holding)).validate()
                    required = domain.regions.task_mask(mapped_atoms)
                    state_index = domain.regions.STATE_INDEX[domain.state_key(mapped_state)]
                    point = state_index, required
                    if point in self.entries:
                        continue
                    if oracle.solve(mapped_atoms, mapped_state, self.horizon).status != "conflict":
                        raise ValueError("unverified state-bound failure")
                    self.entries[point] = {"kind": "state_bound_nogood", "key": key,
                        "source_id": failure["source_id"], "state_index": state_index,
                        "support": [asdict(a) for a in mapped_atoms]}
        for state_index, required in self.entries:
            self.conditions[state_index].append(required)
        for i, requirements in enumerate(self.conditions):
            reduced = []
            for required in sorted(set(requirements), key=lambda b: (b.bit_count(), b)):
                if not any(previous & required == previous for previous in reduced):
                    reduced.append(required)
            self.conditions[i] = tuple(reduced)

    def evidence(self, snapshot):
        if self._token != self.domain.regions.runtime_token():
            raise ValueError("executing domain changed: recertify state cache")
        fixed = self.fixed.evidence(snapshot)
        if fixed is not None:
            return fixed
        available, index = self.domain.features(snapshot)
        for required in self.conditions[index]:
            if required & available == required:
                return self.entries[index, required]
        return None

    def match(self, snapshot, horizon=8):
        if type(horizon) is not int or not 0 <= horizon <= self.horizon:
            return False
        return self.evidence(snapshot) is not None
