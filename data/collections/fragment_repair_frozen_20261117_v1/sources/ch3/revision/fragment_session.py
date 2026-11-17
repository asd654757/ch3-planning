"""Online use of verified failure cores with the original planner as fallback.

The optional exact cache preserves complete executable suffixes, and is limited
to static requests without order clauses. Frozen evaluation cannot grow either
the cache or the core bank. Guarantees inherit the registered finite model.
"""
from dataclasses import dataclass

from ch3.revision.fragments import Oracle
from ch3.revision.fragment_regions import RegionBank, canonical, features, minimize
from ch3.revision.history import Event, Snapshot, decide


def exact_key(snapshot):
    if snapshot.history or any(o.clause.kind == "before" for o in snapshot.contract.obligations):
        return None
    return features(snapshot)


@dataclass(frozen=True)
class Resolution:
    status: str
    plan: tuple[Event, ...]
    origin: str
    new_pattern: str | None = None


class FragmentSession:
    def __init__(self, bank: RegionBank, *, learn=False):
        self.bank = bank
        self.learning = learn
        self.cache_writable = True
        self.memo = {}
        self.oracle = Oracle()
        self.patterns = {}
        self.backend_calls = 0

    def freeze(self):
        self.learning = False
        self.cache_writable = False

    def resolve(self, snapshot: Snapshot):
        # Also checks executing semantics before any cached result can be used.
        if self.bank.match(snapshot, self.bank.horizon):
            return Resolution("conflict", (), "certified_core")
        key = exact_key(snapshot)
        if key is not None and key in self.memo:
            status, plan = self.memo[key]
            return Resolution(status, plan, "exact_cache")
        result = decide(snapshot, snapshot.contract, self.bank.horizon)
        self.backend_calls += 1
        if self.cache_writable and key is not None:
            self.memo[key] = (result.status, result.plan)
        new_key = None
        if self.learning and result.status == "conflict":
            core = minimize(snapshot, self.oracle, self.bank.horizon)
            if core:
                pattern = canonical(core, self.bank.horizon)
                new_bank = RegionBank.verified([pattern], self.oracle, self.bank.horizon)
                self.bank = RegionBank.combine(self.bank, new_bank)
                self.patterns[pattern.key] = pattern
                new_key = pattern.key
        return Resolution(result.status, result.plan, "backend", new_key)
