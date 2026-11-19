"""Version-bound recovery requests and evidence ledger, NOT a safety oracle.

Model output never establishes execution success. Facts below must be supplied
by a declared observation adapter; historical facts are not current facts.
"""
from dataclasses import dataclass
from hashlib import sha256
import json


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RequestTicket:
    request_id: str
    task_version: int
    observation_version: int
    instruction_digest: str
    observation_digest: str


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    task_version: int
    observation_version: int
    skill: str
    object_id: str
    outcome: str
    evidence_source: str


class RecoveryContext:
    """Single-threaded session; recheck a ticket immediately before dispatch.

    Every new observation invalidates pending responses, even identical pixels.
    An instruction update preserves executed history but replaces active goals.
    Unknown observation clears current facts, rather than retaining stale truth.
    """
    def __init__(self, instruction, goals):
        self.task_version = 1
        self.observation_version = 0
        self.instruction = instruction
        self.goals = frozenset(goals)
        self.current_facts = frozenset()
        self.evidence_source = None
        self.observation_digest = None
        self._history = []
        self._requests = {}
        self._sequence = 0

    @property
    def history(self):
        return tuple(self._history)

    @property
    def remaining_goals(self):
        return self.goals - self.current_facts

    def update_task(self, instruction, goals):
        self.task_version += 1
        self.instruction = instruction
        self.goals = frozenset(goals)

    def observe(self, *, facts, image_sha256, evidence_source, status="supported"):
        if status not in {"supported", "unknown"} or not evidence_source or not image_sha256:
            raise ValueError("missing observation identity/source or invalid status")
        if status == "unknown" and facts:
            raise ValueError("unknown observation cannot assert facts")
        self.observation_version += 1
        self.current_facts = frozenset(facts)
        self.evidence_source = evidence_source
        self.observation_digest = digest({"facts": sorted(self.current_facts),
            "image_sha256": image_sha256, "source": evidence_source, "status": status})
        self.observation_status = status

    def issue_request(self):
        if not self.observation_digest or self.observation_status != "supported":
            raise ValueError("planning requires supported current observation")
        self._sequence += 1
        ticket = RequestTicket(f"request-{self._sequence}", self.task_version,
            self.observation_version, digest({"instruction": self.instruction, "goals": sorted(self.goals)}),
            self.observation_digest)
        self._requests[ticket.request_id] = ticket
        return ticket

    def check_request(self, ticket):
        if self._requests.get(ticket.request_id) != ticket:
            raise ValueError("unissued or already consumed request")
        if (ticket.task_version != self.task_version or
            ticket.observation_version != self.observation_version or
            ticket.instruction_digest != digest({"instruction": self.instruction, "goals": sorted(self.goals)}) or
            ticket.observation_digest != self.observation_digest):
            raise ValueError("stale task or observation response")

    def consume_request(self, ticket):
        self.check_request(ticket)
        del self._requests[ticket.request_id]

    def record_execution(self, *, event_id, skill, object_id, outcome, evidence_source):
        if outcome not in {"supported_success", "supported_failure", "unknown"}:
            raise ValueError("invalid execution outcome")
        if not event_id or not evidence_source or any(e.event_id == event_id for e in self.history):
            raise ValueError("missing source/identity or duplicate execution event")
        self._history.append(ExecutionEvent(event_id, self.task_version, self.observation_version,
            skill, object_id, outcome, evidence_source))
        # Intentionally does not insert predicted effects into current_facts.


def recovery_route(*, execution_status, next_prerequisites, intent,
                   unchanged_task_and_state=False, retry_authorized=False,
                   retries_used=0, reobservations_used=0, max_reobservations=2):
    """Bounded routing; inputs are adapter assessments, not model certificates.

    retry_authorized additionally requires a backend-specific recovery gate.
    This function does not authorize motion or infer empty hands.
    """
    if execution_status not in {"not_started", "supported_success", "supported_failure", "unknown"}:
        raise ValueError("invalid execution status")
    if next_prerequisites not in {"supported", "violated", "unknown"} or intent not in {"aligned", "misaligned", "unknown"}:
        raise ValueError("invalid next-action assessment")
    if min(retries_used, reobservations_used, max_reobservations) < 0:
        raise ValueError("negative budget")
    if "unknown" in {execution_status, next_prerequisites, intent}:
        return "REOBSERVE" if reobservations_used < max_reobservations else "STOP"
    if intent == "misaligned" or next_prerequisites == "violated":
        return "REPLAN_FROM_CURRENT_STATE"
    if execution_status == "supported_failure":
        if unchanged_task_and_state and retry_authorized and retries_used == 0:
            return "BOUNDED_FIXED_RETRY"
        return "REPLAN_FROM_CURRENT_STATE"
    return "CONTINUE"
