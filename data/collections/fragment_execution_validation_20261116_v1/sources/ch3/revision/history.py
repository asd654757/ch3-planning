"""Immutable task versions, effective-time semantics, and bounded trace checking.

The solver predicts suffixes; predictions are never appended to the evidence ledger.
`conflict` means infeasible in the supplied finite model and horizon, not in the
physical world. A quoted instruction span proves provenance, not interpretation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from itertools import product
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OBJECTS = ("blue", "yellow")
TARGETS = ("table", "green", "return")


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    id: str = Field(min_length=1)
    kind: Literal["at", "empty", "forbid", "visit", "before"]
    object: str | None = None
    target: str | None = None
    first: str | None = None
    second: str | None = None

    @model_validator(mode="after")
    def scope(self):
        expected = {"at": {"object", "target"}, "visit": {"object", "target"},
                    "forbid": {"object"}, "empty": set(),
                    "before": {"first", "second"}}[self.kind]
        actual = {k for k in ("object", "target", "first", "second") if getattr(self, k) is not None}
        if actual != expected:
            raise ValueError("clause fields do not match kind")
        if self.object is not None and self.object not in OBJECTS:
            raise ValueError("unknown object")
        if self.target is not None and self.target not in TARGETS:
            raise ValueError("unknown target")
        if self.kind == "before" and self.first == self.second:
            raise ValueError("self order")
        return self


@dataclass(frozen=True)
class Obligation:
    clause: Clause
    since: int = 0


@dataclass(frozen=True)
class Contract:
    version: int
    obligations: tuple[Obligation, ...]

    def validate(self):
        if type(self.version) is not int or self.version < 0:
            raise ValueError("invalid task version")
        by_id = {o.clause.id: o for o in self.obligations}
        if len(by_id) != len(self.obligations):
            raise ValueError("duplicate clause id")
        for o in self.obligations:
            if type(o.since) is not int or o.since < 0:
                raise ValueError("negative effective time")
            c = o.clause
            if c.kind == "before":
                for ref in (c.first, c.second):
                    if ref not in by_id or by_id[ref].clause.kind != "visit":
                        raise ValueError("order must reference existing visit clauses")
        return self


@dataclass(frozen=True)
class Event:
    seq: int
    kind: Literal["pick", "place"]
    object: str
    target: str | None
    evidence: Literal["confirmed", "unknown", "refuted"] = "confirmed"
    observation_id: str = "symbolic-fixture"


@dataclass(frozen=True)
class State:
    blue: str = "table"
    yellow: str = "table"
    holding: str | None = None

    def validate(self):
        if self.holding is not None and self.holding not in OBJECTS:
            raise ValueError("unknown held object")
        for obj in OBJECTS:
            if getattr(self, obj) not in (*TARGETS, "hand"):
                raise ValueError("unknown location")
            if (getattr(self, obj) == "hand") != (self.holding == obj):
                raise ValueError("holding/location disagreement")
        for region in ("green", "return"):
            if sum(getattr(self, obj) == region for obj in OBJECTS) > 1:
                raise ValueError("region capacity exceeded")
        return self


@dataclass(frozen=True)
class Snapshot:
    contract: Contract
    state: State
    history: tuple[Event, ...]
    observation_id: str

    def validate(self):
        self.contract.validate()
        self.state.validate()
        previous = 0
        unknown = 0
        for e in self.history:
            if (type(e.seq) is not int or e.seq <= previous or e.object not in OBJECTS
                    or not isinstance(e.observation_id, str) or not e.observation_id):
                raise ValueError("invalid evidence ledger")
            if e.kind not in ("pick", "place") or e.evidence not in ("confirmed", "unknown", "refuted"):
                raise ValueError("invalid event type")
            if (e.kind == "place" and e.target not in TARGETS) or (e.kind == "pick" and e.target is not None):
                raise ValueError("invalid event target")
            previous = e.seq
            unknown += e.evidence == "unknown"
        if unknown > 6:
            raise ValueError("at most six uncertain events in this bounded model")
        if any(o.since > self.cursor for o in self.contract.obligations):
            raise ValueError("effective time exceeds history cursor")
        if not isinstance(self.observation_id, str) or not self.observation_id:
            raise ValueError("missing observation binding")
        return self

    @property
    def cursor(self):
        return self.history[-1].seq if self.history else 0

    @property
    def digest(self):
        return sha256(json.dumps(asdict(self), default=lambda x: x.model_dump(),
                                 sort_keys=True).encode()).hexdigest()


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    op: Literal["add", "replace", "remove"]
    id: str
    quote: str = Field(min_length=1)
    clause: Clause | None = None


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: int
    snapshot: str
    edits: list[Edit]


class Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: int
    snapshot: str
    clauses: list[Clause]


class SlicedReplacement(Replacement):
    changed_ids: list[str]


def _anchor(old: dict[str, Obligation], c: Clause, cursor: int):
    previous = old.get(c.id)
    # Effective times are assigned by the program, equally for both methods.
    if previous and previous.clause == c:
        return previous
    return Obligation(c, cursor)


def _bound(snapshot: Snapshot, version: int, digest: str):
    snapshot.validate()
    if version != snapshot.contract.version or digest != snapshot.digest:
        raise ValueError("stale task/state/history response")


def apply_patch(snapshot: Snapshot, patch: Patch, instruction: str) -> Contract:
    _bound(snapshot, patch.version, patch.snapshot)
    old = {o.clause.id: o for o in snapshot.contract.obligations}
    revised = dict(old)
    touched = set()
    for edit in patch.edits:
        if edit.id in touched or edit.quote not in instruction:
            raise ValueError("duplicate edit or unsupported source quote")
        touched.add(edit.id)
        if edit.op == "add" and edit.id in old:
            raise ValueError("add overwrites an existing clause")
        if edit.op != "add" and edit.id not in old:
            raise ValueError("edit refers to an absent clause")
        if edit.op == "remove":
            if edit.clause is not None:
                raise ValueError("remove contains a clause")
            del revised[edit.id]
        else:
            if edit.clause is None or edit.clause.id != edit.id:
                raise ValueError("edit id/clause mismatch")
            revised[edit.id] = _anchor(old, edit.clause, snapshot.cursor)
    return Contract(snapshot.contract.version + 1, tuple(revised.values())).validate()


def apply_replacement(snapshot: Snapshot, replacement: Replacement) -> Contract:
    _bound(snapshot, replacement.version, replacement.snapshot)
    old = {o.clause.id: o for o in snapshot.contract.obligations}
    return Contract(snapshot.contract.version + 1,
                    tuple(_anchor(old, c, snapshot.cursor) for c in replacement.clauses)).validate()


def apply_sliced_replacement(snapshot: Snapshot, replacement: SlicedReplacement) -> Contract:
    """Strong control: a full translation plus a declared write set.

    This uses the same preservation mechanism as a patch, without edit syntax.
    It prevents attributing a generic write-set benefit to a new patch algorithm.
    """
    _bound(snapshot, replacement.version, replacement.snapshot)
    old = {o.clause.id: o.clause for o in snapshot.contract.obligations}
    proposed = {c.id: c for c in replacement.clauses}
    changed = set(replacement.changed_ids)
    if len(proposed) != len(replacement.clauses) or len(changed) != len(replacement.changed_ids):
        raise ValueError("duplicate full clause or changed id")
    if not changed <= old.keys() | proposed.keys():
        raise ValueError("changed id absent from both tasks")
    merged = {k: c for k, c in old.items() if k not in changed}
    merged.update({k: c for k, c in proposed.items() if k in changed})
    return apply_replacement(snapshot, Replacement(version=replacement.version,
        snapshot=replacement.snapshot, clauses=list(merged.values())))


def semantic_key(contract: Contract):
    """ID-independent comparison; historical anchors ARE semantic information."""
    by_id = {o.clause.id: o for o in contract.obligations}
    def key(o):
        c = o.clause
        if c.kind == "before":
            return (c.kind, key(by_id[c.first]), key(by_id[c.second]))
        return (c.kind, c.object, c.target, o.since if c.kind in ("visit", "forbid") else None)
    return sorted((key(o) for o in contract.obligations), key=repr)


def possible_histories(history: tuple[Event, ...]):
    uncertain = [e.seq for e in history if e.evidence == "unknown"]
    for bits in product((False, True), repeat=len(uncertain)):
        included = dict(zip(uncertain, bits))
        yield tuple(e for e in history if e.evidence == "confirmed" or
                    (e.evidence == "unknown" and included[e.seq]))


def monitor(contract: Contract, state: State, trace: tuple[Event, ...]):
    visits = {}
    violated = set()
    pending = set()
    for o in contract.obligations:
        c = o.clause
        eligible = [e for e in trace if e.seq > o.since]
        if c.kind == "visit":
            hits = [e.seq for e in eligible if e.kind == "place" and
                    e.object == c.object and e.target == c.target]
            visits[c.id] = min(hits) if hits else None
            if not hits:
                pending.add(c.id)
        elif c.kind == "forbid":
            if any(e.object == c.object for e in eligible):
                violated.add(c.id)
        elif c.kind == "at" and getattr(state, c.object) != c.target:
            pending.add(c.id)
        elif c.kind == "empty" and state.holding is not None:
            pending.add(c.id)
    for o in contract.obligations:
        c = o.clause
        if c.kind == "before":
            a, b = visits[c.first], visits[c.second]
            if b is not None and (a is None or a >= b):
                violated.add(c.id)
    return frozenset(violated), frozenset(pending)


def successors(state: State, seq: int):
    if state.holding is None:
        for obj in OBJECTS:
            yield replace(state, **{obj: "hand", "holding": obj}), Event(seq, "pick", obj, None)
    else:
        obj = state.holding
        for target in TARGETS:
            if target != "table" and any(getattr(state, other) == target for other in OBJECTS if other != obj):
                continue
            yield replace(state, **{obj: target, "holding": None}), Event(seq, "place", obj, target)


@dataclass(frozen=True)
class Decision:
    status: Literal["ready", "conflict", "need_observation"]
    plan: tuple[Event, ...]
    history_alternatives: int
    feasible_alternatives: int
    horizon: int
    disputed_events: tuple[int, ...] = ()
    violated_clauses: tuple[str, ...] = ()


def _search(contract, state, histories, cursor, horizon):
    queue = deque([(state, (), histories)])
    seen = set()
    while queue:
        current, plan, traces = queue.popleft()
        checks = [monitor(contract, current, t) for t in traces]
        if any(v for v, p in checks):
            continue
        if all(not p for v, p in checks):
            return plan
        if len(plan) == horizon:
            continue
        # Future behavior depends on reached state and clause progress, not exact time.
        progress = []
        for trace in traces:
            visits = []
            for o in contract.obligations:
                c = o.clause
                if c.kind == "visit":
                    hits = [e.seq for e in trace if e.seq > o.since and e.kind == "place"
                            and e.object == c.object and e.target == c.target]
                    visits.append((c.id, bool(hits)))
            progress.append(tuple(visits))
        key = (current, tuple(progress))
        if key in seen:
            continue
        seen.add(key)
        for next_state, event in successors(current, cursor + len(plan) + 1):
            queue.append((next_state, (*plan, event), tuple((*t, event) for t in traces)))
    return None


def decide(snapshot: Snapshot, contract: Contract, horizon: int = 8) -> Decision:
    snapshot.validate()
    contract.validate()
    if type(horizon) is not int or not 0 <= horizon <= 12:
        raise ValueError("horizon outside bounded model")
    if any(o.since > snapshot.cursor for o in contract.obligations):
        raise ValueError("effective time exceeds history cursor")
    histories = tuple(possible_histories(snapshot.history))
    common = _search(contract, snapshot.state, histories, snapshot.cursor, horizon)
    if common is not None:
        return Decision("ready", common, len(histories), len(histories), horizon)
    feasible = sum(_search(contract, snapshot.state, (h,), snapshot.cursor, horizon) is not None
                   for h in histories)
    violated = sorted(set().union(*(monitor(contract, snapshot.state, h)[0] for h in histories)))
    return Decision("need_observation" if feasible else "conflict", (), len(histories), feasible,
                    horizon, tuple(e.seq for e in snapshot.history if e.evidence == "unknown"),
                    tuple(violated))


@dataclass(frozen=True)
class RevisionResult:
    """Only ready contracts may execute; pending changes suspend the old task."""
    proposed: Contract
    decision: Decision

    @property
    def executable(self):
        return self.decision.status == "ready"
