"""Evidence-bound interface from revision contracts to registered robot actions.

This is a finite-domain dispatch adapter, not a physical observation detector.
Only the observation adapter can supply state/events; command receipts and
predicted suffixes never update the ledger. Unknown current state must stop
upstream, rather than being coerced to table/empty hand here.
"""
from dataclasses import dataclass, replace

from ch3.revision.evaluation import valid_suffix
from ch3.revision.history import Decision, Event, Snapshot, State, decide, successors
from ch3.schema.model_plan import ModelPlanAction

OBJECT_IDS = {"blue": "blue_candidate", "yellow": "yellow_candidate"}
TARGET_IDS = {"table": "table", "green": "green_region", "return": "return_region"}


def observed_state(world, *, supported_objects, holding_supported):
    """Explicit coverage is mandatory; an absent WorldState.holding is not proof."""
    if set(supported_objects) != set(OBJECT_IDS.values()) or not holding_supported:
        raise ValueError("incomplete current-state evidence")
    if set(world.holding) - {"right"}:
        raise ValueError("revision domain supports only the right hand")
    inverse = {v: k for k, v in OBJECT_IDS.items()}
    targets = {v: k for k, v in TARGET_IDS.items()}
    held_id = world.holding.get("right")
    if held_id is not None and held_id not in inverse:
        raise ValueError("unregistered held object")
    locations = {}
    for obj, object_id in OBJECT_IDS.items():
        if object_id not in world.objects:
            raise ValueError("missing registered object")
        if held_id == object_id:
            locations[obj] = "hand"
        elif world.location_of(object_id) in targets:
            locations[obj] = targets[world.location_of(object_id)]
        else:
            raise ValueError("unknown observed support; no implicit table default")
    return State(**locations, holding=inverse.get(held_id)).validate()


def rebase_suffix(snapshot, plan):
    """Rebind predicted action indices, never their effects, to the new cursor."""
    return tuple(Event(snapshot.cursor+i+1, e.kind, e.object, e.target,
                       observation_id="prediction-only") for i, e in enumerate(plan))


def routed_decision(snapshot, contract, old_suffix=(), horizon=8):
    candidate = rebase_suffix(snapshot, old_suffix)
    if len(candidate) <= horizon and valid_suffix(snapshot, contract, candidate):
        alternatives = 2**sum(e.evidence == "unknown" for e in snapshot.history)
        return Decision("ready", candidate, alternatives, alternatives, horizon), "REUSE_VALID_SUFFIX"
    result = decide(snapshot, contract, horizon)
    return result, ("BOUNDED_REPLAN" if result.status == "ready" else "SUSPEND")


@dataclass(frozen=True)
class DispatchTicket:
    snapshot_digest: str
    generation: int
    action: Event


class RevisionRuntime:
    """One atomic skill per dispatch; every observation invalidates old tickets.

    The caller must still compile/check actions against backend capability and
    physical gates. This adapter supplies neither inverse kinematics nor motion.
    """
    def __init__(self, snapshot, horizon=8):
        self.snapshot = snapshot.validate()
        self.horizon = horizon
        self.generation = 0
        self.decision = None
        self.pending = False
        self._ticket = None

    def accept(self, contract, *, expected_digest, expected_generation, old_suffix=(), supplied_decision=None):
        if expected_digest != self.snapshot.digest or expected_generation != self.generation:
            raise ValueError("stale revision response")
        if contract.version != self.snapshot.contract.version+1:
            raise ValueError("nonconsecutive task version")
        # Even a conflicting new intent suspends execution of the old task.
        proposed = replace(self.snapshot, contract=contract).validate()
        if supplied_decision is None:
            result, route = routed_decision(proposed, contract, old_suffix, self.horizon)
        else:
            result, route = supplied_decision, "VALIDATED_EXTERNAL_DECISION"
            if result.status == "ready":
                if len(result.plan) > self.horizon or not valid_suffix(proposed, contract, result.plan):
                    raise ValueError("invalid externally supplied suffix")
            elif result.plan or decide(proposed, contract, self.horizon).status != result.status:
                raise ValueError("invalid externally supplied decision")
        self.snapshot = proposed
        self.decision = result
        self.pending = result.status != "ready"
        self.generation += 1
        self._ticket = None
        return result, route

    def observe(self, state, *, observation_id, events=()):
        """Append only externally supplied evidence. No inferred command effects."""
        if any(e.seq <= self.snapshot.cursor for e in events):
            raise ValueError("observation cannot overwrite historical events")
        updated = replace(self.snapshot, state=state,
                          history=(*self.snapshot.history, *events), observation_id=observation_id).validate()
        self.snapshot = updated
        self.generation += 1
        self._ticket = None
        self.decision = None

    def assess(self, old_suffix=(), *, allow_replan=True):
        if not allow_replan:
            candidate = rebase_suffix(self.snapshot, old_suffix)
            if len(candidate) > self.horizon or not valid_suffix(self.snapshot, self.snapshot.contract, candidate):
                raise ValueError("supplied suffix invalid; no automatic solver repair")
            alternatives = 2**sum(e.evidence == "unknown" for e in self.snapshot.history)
            result = Decision("ready", candidate, alternatives, alternatives, self.horizon)
            self.decision, self.pending = result, False
            return result, "VALIDATED_EXTERNAL_SUFFIX"
        result, route = routed_decision(self.snapshot, self.snapshot.contract, old_suffix, self.horizon)
        self.decision = result
        self.pending = result.status != "ready"
        return result, route

    def issue(self):
        if self.pending or self.decision is None or self.decision.status != "ready":
            raise ValueError("no executable assessed revision")
        if not self.decision.plan:
            return None
        if self._ticket is not None:
            raise ValueError("one outstanding dispatch at a time")
        self._ticket = DispatchTicket(self.snapshot.digest, self.generation, self.decision.plan[0])
        return self._ticket

    def consume(self, ticket):
        if (ticket is None or ticket != self._ticket or ticket.generation != self.generation
                or ticket.snapshot_digest != self.snapshot.digest):
            raise ValueError("stale, unissued or consumed dispatch")
        event = ticket.action
        if not any((e.kind, e.object, e.target) == (event.kind, event.object, event.target)
                   for _, e in successors(self.snapshot.state, self.snapshot.cursor+1)):
            raise ValueError("current action prerequisite violated")
        action = ModelPlanAction(step_id=1, skill=event.kind, arm="right",
            object_id=OBJECT_IDS[event.object], target_id=TARGET_IDS.get(event.target))
        self._ticket = None
        # No second command until another supported observation and assessment.
        self.decision = None
        return action
