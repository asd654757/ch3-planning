"""Semantic execution of the one registered LIBERO soup-to-basket task."""

from dataclasses import asdict, dataclass

import numpy as np

from ch3.revision.evidence_resolution import EvidenceFinding, EvidenceRevisionRuntime
from ch3.revision.history import Clause, Contract, Event, Obligation, Snapshot, State


# The finite ROUTED domain has fixed slots rather than arbitrary entity names.
OBJECT_SLOT, TARGET_SLOT = "blue", "green"
CAN, BASKET, REGION = "alphabet_soup_1", "basket_1", "basket_1_contain_region"
EVIDENCE_SOURCE = "privileged_mujoco_geometry_and_contacts"
FLOOR = State()
HELD = State(blue="hand", holding="blue")
PLACED = State(blue="green")


@dataclass(frozen=True)
class ManipulationSample:
    lift_m: float
    left_pad: bool
    right_pad: bool
    inside_basket: bool
    basket_contact: bool
    speed_m_s: float

    @property
    def supports_pick(self):
        return self.left_pad and self.right_pad and self.lift_m >= 0.022

    @property
    def released_in_basket(self):
        return self.inside_basket and not (self.left_pad or self.right_pad)

    @property
    def supports_place(self):
        return self.released_in_basket and self.basket_contact and self.speed_m_s <= 0.05

    @property
    def settled_on_floor(self):
        return (not self.inside_basket and not (self.left_pad or self.right_pad)
                and abs(self.lift_m) <= 0.018 and self.speed_m_s <= 0.05)


class MujocoSoupObserver:
    """Isolate the registered physical measurements from LIBERO task scoring."""

    def __init__(self, raw_environment):
        self.env = raw_environment
        self.sim = raw_environment.sim
        self.can = raw_environment.get_object(CAN)
        self.basket = raw_environment.get_object(BASKET)
        self.body_id = raw_environment.obj_body_id[CAN]
        self.region_id = self.sim.model.site_name2id(REGION)
        self.initial_z = float(self.sim.data.body_xpos[self.body_id][2])
        pads = raw_environment.robots[0].gripper.important_geoms
        self.left, self.right = pads["left_fingerpad"], pads["right_fingerpad"]

    def read(self):
        data = self.sim.data
        position = data.body_xpos[self.body_id]
        local = (position - data.get_site_xpos(REGION)) @ data.get_site_xmat(REGION)
        return ManipulationSample(float(position[2] - self.initial_z),
                                  bool(self.env.check_contact(self.left, self.can.contact_geoms)),
                                  bool(self.env.check_contact(self.right, self.can.contact_geoms)),
                                  bool(np.all(np.abs(local) <= self.sim.model.site_size[self.region_id])),
                                  bool(self.env.check_contact(self.can.contact_geoms, self.basket.contact_geoms)),
                                  float(np.linalg.norm(data.get_body_xvelp(self.can.root_body))))


class LiberoSemanticSupervisor:
    """Open and finish native-policy phases using the original ROUTED runtime."""

    def __init__(self, initial_sample, emit):
        if not initial_sample.settled_on_floor:
            raise ValueError("initial soup can is outside the registered pickup condition")
        self.emit = emit
        self.runtime = EvidenceRevisionRuntime(Snapshot(Contract(0, ()), FLOOR, (), "reset-supported"))
        task = Contract(1, tuple(Obligation(c) for c in (
            Clause(id="deliver", kind="at", object=OBJECT_SLOT, target=TARGET_SLOT),
            Clause(id="visit", kind="visit", object=OBJECT_SLOT, target=TARGET_SLOT),
            Clause(id="release", kind="empty"),
            Clause(id="inactive_slot", kind="forbid", object="yellow"),
        )))
        self.decision, route = self.runtime.accept(task, expected_digest=self.runtime.snapshot.digest,
                                                   expected_generation=self.runtime.generation)
        self._emit_decision(route)
        self.active = None
        self.disputed = None

    def _emit_decision(self, route):
        self.emit({"event": "task_decision", "route": route, "status": self.decision.status,
                   "version": self.runtime.snapshot.contract.version,
                   "generation": self.runtime.generation,
                   "suffix": [{"kind": action.kind, "target": action.target} for action in self.decision.plan]})

    def authorize(self):
        if self.active is not None or self.disputed is not None:
            raise ValueError("phase already active or awaiting evidence")
        ticket = self.runtime.issue()
        if ticket is None:
            return None
        self.active = self.runtime.consume(ticket)
        if (self.active.skill, self.active.target_id) not in (("pick", None), ("place", "green_region")):
            raise ValueError("unsupported native-policy phase")
        self.emit({"event": "authorize_phase", "skill": self.active.skill, "object": CAN,
                   "target": BASKET if self.active.skill == "place" else None,
                   "generation": ticket.generation, "snapshot_digest": ticket.snapshot_digest})
        return self.active.skill

    def _event(self, target, observation_id, evidence="confirmed"):
        if self.active is None:
            raise ValueError("no phase to observe")
        return Event(self.runtime.snapshot.cursor + 1, self.active.skill, OBJECT_SLOT, target,
                     observation_id=observation_id, evidence=evidence)

    def await_pick_evidence(self, observation_id):
        if self.active is None or self.active.skill != "pick" or self.disputed is not None:
            raise ValueError("only the outstanding pick can be disputed")
        self.disputed = self._event(None, observation_id, "unknown")
        # Retain the latest supported state while refusing to dispatch based on it.
        self.runtime.observe(self.runtime.snapshot.state, observation_id=observation_id, events=(self.disputed,))
        self.emit({"event": "need_observation", "disputed_event": asdict(self.disputed),
                   "generation": self.runtime.generation})

    def confirm_pick(self, sample, observation_id):
        if self.active is None or self.active.skill != "pick" or not sample.supports_pick:
            raise ValueError("pick requires bilateral contact and lift")
        if self.disputed is not None:
            finding = EvidenceFinding(self.disputed.seq, "confirmed", observation_id,
                                      self.runtime.snapshot.digest, self.runtime.generation)
            self.runtime.resolve_evidence(finding)
            self.emit({"event": "resolve_evidence", "finding": asdict(finding)})
            self.disputed = None
            self.runtime.observe(HELD, observation_id=observation_id)
            self._assess(self.decision.plan[1:])
        else:
            self._commit(HELD, None, observation_id)

    def confirm_place(self, sample, observation_id):
        if self.active is None or self.active.skill != "place" or not sample.supports_place:
            raise ValueError("place requires released and settled basket contact")
        self._commit(PLACED, TARGET_SLOT, observation_id)

    def observe_drop(self, sample, observation_id):
        if self.active is None or self.active.skill != "place" or not sample.settled_on_floor:
            raise ValueError("drop recovery requires settled floor evidence")
        self._commit(FLOOR, "table", observation_id, retain_suffix=False)

    def _commit(self, state, target, observation_id, retain_suffix=True):
        event = self._event(target, observation_id)
        self.runtime.observe(state, observation_id=observation_id, events=(event,))
        self.emit({"event": "record_observed_effect", "effect": asdict(event), "source": EVIDENCE_SOURCE})
        self._assess(self.decision.plan[1:] if retain_suffix else ())

    def _assess(self, suffix):
        self.active = None
        self.decision, route = self.runtime.assess(suffix)
        self._emit_decision(route)
