"""Exact static-core certification by reverse product-graph search.

One traversal supplies all initial states. Typed domain automorphisms share
profiles between object names and the two interchangeable unit-capacity areas.
Plans are reconstructed in ORIGINAL successor order, preserving BFS tie breaks.
This changes certification cost, not the full-task/history planning backend.
"""
from collections import deque
from hashlib import sha256
from itertools import permutations
from pathlib import Path
from time import perf_counter

from ch3.revision.fragments import Atom
from ch3.revision.fragment_domain_adapter import build_domain


class BatchOracle:
    def __init__(self, domain):
        self.domain = domain
        self.token = domain.regions.runtime_token()
        self.calls = 0
        self.elapsed_ms = 0.0
        self.profiles = {}
        self.views = {}
        self.product_nodes_visited = 0
        self.product_edges_examined = 0
        self.states = domain.states
        self.index = {domain.state_key(s): i for i, s in enumerate(self.states)}
        self.edges = [tuple((self.index[domain.state_key(ns)], e)
                           for ns, e in domain.model.successors(s, 1)) for s in self.states]

    def stats(self):
        return {"logical_solve_requests": self.calls, "profile_searches": len(self.profiles),
                "binding_views": len(self.views), "product_nodes_visited": self.product_nodes_visited,
                "product_edges_examined": self.product_edges_examined,
                "solve_elapsed_ms": self.elapsed_ms}

    def _canonical(self, atoms):
        objects = self.domain.model.OBJECTS
        best = None
        best_maps = None
        for order in permutations(objects):
            names = dict(zip(objects, order))
            for areas in (("green", "return"), ("return", "green")):
                places = {"table": "table", "hand": "hand", "": "",
                          **dict(zip(("green", "return"), areas))}
                renamed = tuple(sorted(Atom(a.kind, names.get(a.object, ""), places[a.target]) for a in atoms))
                if best is None or renamed < best:
                    best, best_maps = renamed, (names, places)
        return best, best_maps

    def _profile(self, atoms):
        visits = tuple(a for a in atoms if a.kind == "visit")
        visit_bits = {(a.object, a.target): 1 << i for i, a in enumerate(visits)}
        masks = 1 << len(visits)
        protected = {a.object for a in atoms if a.kind == "forbid"}
        goals = [a for a in atoms if a.kind == "at"]
        empty = any(a.kind == "empty" for a in atoms)
        incoming = [[] for _ in self.states]
        for i, edges in enumerate(self.edges):
            for j, event in edges:
                if event.object in protected:
                    continue
                bit = visit_bits.get((event.object, event.target), 0) if event.kind == "place" else 0
                incoming[j].append((i, bit))
        # -1 denotes no path; each product node is visited at most once.
        distance = [-1] * (len(self.states) * masks)
        queue = deque()
        for i, state in enumerate(self.states):
            if (not empty or state.holding is None) and all(getattr(state, a.object) == a.target for a in goals):
                node = i * masks + masks - 1
                distance[node] = 0
                queue.append(node)
        while queue:
            node = queue.popleft()
            state, completed = divmod(node, masks)
            self.product_nodes_visited += 1
            for predecessor, bit in incoming[state]:
                self.product_edges_examined += 1
                if bit and not completed & bit:
                    continue
                previous_masks = (completed, completed ^ bit) if bit else (completed,)
                for previous in previous_masks:
                    prior = predecessor * masks + previous
                    if distance[prior] == -1:
                        distance[prior] = distance[node] + 1
                        queue.append(prior)
        return {"distance": tuple(distance), "masks": masks, "visit_bits": visit_bits}

    def _view(self, atoms):
        canonical, (names, places) = self._canonical(atoms)
        if canonical not in self.profiles:
            self.profiles[canonical] = self._profile(canonical)
        profile = self.profiles[canonical]
        state_map = []
        for state in self.states:
            locations = {names[obj]: places[getattr(state, obj)] for obj in self.domain.model.OBJECTS}
            state_map.append(self.index[tuple(locations[obj] for obj in self.domain.model.OBJECTS)])
        bits = {}
        for obj in self.domain.model.OBJECTS:
            for target in self.domain.model.TARGETS:
                bits[obj, target] = profile["visit_bits"].get((names[obj], places[target]), 0)
        return profile, tuple(state_map), bits, {a.object for a in atoms if a.kind == "forbid"}

    def solve(self, atoms, state, horizon):
        if self.domain.regions.runtime_token() != self.token:
            raise ValueError("executing domain changed: rebuild batch oracle")
        if type(horizon) is not int or not 0 <= horizon <= 12:
            raise ValueError("horizon outside bounded model")
        state.validate()
        started = perf_counter()
        self.calls += 1
        try:
            # The old Oracle's contract ignores location facts. Keep that API.
            key = tuple(sorted(set(a for a in atoms if a.kind != "loc")))
            if key not in self.views:
                self.domain.old.contract(key)  # identical schema/type validation
                self.views[key] = self._view(key)
            profile, state_map, bits, protected = self.views[key]
            index = self.index[self.domain.state_key(state)]
            masks = profile["masks"]
            distance = profile["distance"]
            remaining = distance[state_map[index] * masks]
            if remaining < 0 or remaining > horizon:
                return self.domain.model.Decision("conflict", (), 1, 0, horizon)
            completed = 0
            plan = []
            event_type = self.domain.model.Event
            while remaining:
                for successor, event in self.edges[index]:
                    if event.object in protected:
                        continue
                    new_completed = completed | (bits[event.object, event.target] if event.kind == "place" else 0)
                    if distance[state_map[successor] * masks + new_completed] == remaining - 1:
                        plan.append(event_type(len(plan) + 1, event.kind, event.object, event.target,
                                               event.evidence, event.observation_id))
                        index, completed = successor, new_completed
                        remaining -= 1
                        break
                else:
                    raise AssertionError("reverse distance has no forward witness")
            return self.domain.model.Decision("ready", tuple(plan), 1, 1, horizon)
        finally:
            self.elapsed_ms += (perf_counter() - started) * 1000


def build_batch_domain(objects=("blue", "yellow", "red")):
    """Install the shared oracle ONLY inside a fresh isolated domain instance."""
    domain = build_domain(objects)
    base_fingerprint = domain.fingerprint

    def fingerprint():
        return sha256(base_fingerprint().encode() + Path(__file__).read_bytes()).hexdigest()

    domain.fingerprint = fingerprint
    region_globals = domain.regions.RegionBank.__init__.__globals__
    region_globals["source_fingerprint"] = fingerprint
    generic_asdict = region_globals["asdict"]

    def certificate_asdict(value):
        # Event fields are immutable primitives; avoid recursive dataclass
        # reflection for every witness step. All other records keep asdict.
        if type(value) is domain.model.Event:
            return {"seq": value.seq, "kind": value.kind, "object": value.object,
                    "target": value.target, "evidence": value.evidence,
                    "observation_id": value.observation_id}
        return generic_asdict(value)

    region_globals["asdict"] = certificate_asdict
    domain.regions.source_fingerprint = fingerprint
    domain.batch_oracles = []

    class DomainOracle(BatchOracle):
        def __init__(self):
            super().__init__(domain)
            domain.batch_oracles.append(self)

    domain.old.Oracle = DomainOracle
    domain.Session.__init__.__globals__["Oracle"] = DomainOracle
    return domain
