"""Compact exact distance certificates using the shared batch oracle.

All registered bindings and states remain checked. Full feasible witness paths
are reconstructed for audit, rather than duplicated in every certificate row.
Serialized distances and conflict masks are ALWAYS recomputed on untrusted load.
"""
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from ch3.revision.batch_fragment_oracle import BatchOracle, build_batch_domain


class CompactOracle(BatchOracle):
    def __init__(self, domain):
        super().__init__(domain)
        self.profile_requests = 0
        self.profile_state_results = 0
        self.profile_elapsed_ms = 0.0

    def distances(self, atoms):
        if self.domain.regions.runtime_token() != self.token:
            raise ValueError("executing domain changed: rebuild batch oracle")
        started = perf_counter()
        try:
            key = tuple(sorted(set(a for a in atoms if a.kind != "loc")))
            if key not in self.views:
                self.domain.old.contract(key)
                self.views[key] = self._view(key)
            profile, state_map, _, _ = self.views[key]
            self.profile_requests += 1
            self.profile_state_results += len(self.states)
            return tuple(profile["distance"][state_map[i] * profile["masks"]] for i in range(len(self.states)))
        finally:
            self.profile_elapsed_ms += (perf_counter() - started) * 1000

    def stats(self):
        result = super().stats()
        result.update(single_state_solve_requests=self.calls,
                      batch_profile_requests=self.profile_requests,
                      batched_state_results=self.profile_state_results,
                      total_logical_state_queries=self.calls + self.profile_state_results,
                      profile_elapsed_ms=self.profile_elapsed_ms,
                      total_oracle_elapsed_ms=self.elapsed_ms + self.profile_elapsed_ms)
        return result


def build_compact_domain(objects=("blue", "yellow", "red")):
    domain = build_batch_domain(objects)
    base_fingerprint = domain.fingerprint

    def fingerprint():
        return sha256(base_fingerprint().encode() + Path(__file__).read_bytes()).hexdigest()

    def certify_region(pattern, oracle):
        used = {term for a in pattern.atoms for term in (a.object, a.target) if term.startswith("?")}
        if used != {v for v, _ in pattern.variables} or len(used) != len(pattern.variables):
            raise ValueError("variable declaration mismatch")
        if not pattern.atoms or any(a.kind not in ("at", "visit", "forbid", "empty") for a in pattern.atoms):
            raise ValueError("invalid task core")
        if type(pattern.horizon) is not int or not 0 <= pattern.horizon <= 12:
            raise ValueError("invalid horizon")
        bindings = []
        for binding in domain.regions.assignments(pattern):
            atoms = domain.old.instantiate(pattern, binding)
            domain.old.contract(atoms)
            distances = oracle.distances(atoms)
            bad = sum(1 << i for i, length in enumerate(distances)
                      if length < 0 or length > pattern.horizon)
            bindings.append({"binding": binding, "atoms": [asdict(a) for a in atoms],
                             "bad_state_mask": bad, "minimum_actions": distances})
        if not bindings or not any(b["bad_state_mask"] for b in bindings):
            raise ValueError("core has no certified conflict region")
        return {"pattern": asdict(pattern), "key": pattern.key, "bindings": bindings,
                "format": "exact_distance_vector_v1",
                "scope": "maximal_state_guard_for_this_core_in_registered_finite_domain",
                "semantics_sha256": fingerprint()}

    domain.fingerprint = fingerprint
    namespace = domain.regions.RegionBank.__init__.__globals__
    namespace["source_fingerprint"] = fingerprint
    namespace["certify_region"] = certify_region
    domain.regions.source_fingerprint = fingerprint
    domain.regions.certify_region = certify_region
    # Certification representation changes; preserve an honest algorithm audit.
    domain.unchanged_algorithms["regions.certify_region"] = False
    domain.batch_oracles = []

    class DomainOracle(CompactOracle):
        def __init__(self):
            super().__init__(domain)
            domain.batch_oracles.append(self)

    domain.old.Oracle = DomainOracle
    domain.Session.__init__.__globals__["Oracle"] = DomainOracle
    return domain
