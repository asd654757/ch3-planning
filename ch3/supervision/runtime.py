"""Bounded observation/dispatch loop shared by mock and future robot backends."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Protocol

from .core import Observation, PolicyAdapter, Supervisor


class Observer(Protocol):
    def read(self) -> Observation: ...


@dataclass(frozen=True)
class Session:
    supervisor: Supervisor
    observer: Observer
    backend: PolicyAdapter
    # Explicit provenance, not an inference made from the adapter class name.
    evidence_kind: str

    def __post_init__(self):
        if self.evidence_kind not in {"mock_integration_only", "symbolic_only", "simulator", "robot"}:
            raise ValueError("explicit evidence kind required")


def run_session(session: Session, *, max_observations: int = 20,
                emit: Callable[[dict], None] | None = None) -> dict:
    """No background polling: at most one read and one command per iteration.

    On adapter/observer error, stop and report only the exception type. In
    particular an uncertain command is NOT retransmitted automatically.
    External adapters must enforce their own IO timeouts and resource cleanup.
    """
    if type(max_observations) is not int or max_observations < 1:
        raise ValueError("positive integer observation budget required")
    supervisor = session.supervisor
    emit = emit or (lambda event: None)
    observations = 0
    reason = "observation_budget_exhausted"
    error_type = None
    audit_cursor = len(supervisor.audit)
    emit({"event": "session_start", "evidence_kind": session.evidence_kind,
          "max_observations": max_observations,
          "instruction": supervisor.task.instruction,
          "goal_facts": list(supervisor.task.goal_facts)})
    for _ in range(max_observations):
        try:
            observation = session.observer.read()
            supervisor.observe(observation)
        except Exception as exc:
            reason, error_type = "observation_error", type(exc).__name__
            break
        observations += 1
        emit({"event": "observation", "episode_id": observation.episode_id,
              "sequence": observation.sequence, "occupancy": dict(observation.occupancy),
              "observed_facts": sorted(observation.state.facts()),
              "images": list(observation.images),
              "goal_evidence": {fact: {"value": evidence.value.value,
                  "source": evidence.source, "detail": evidence.detail}
                  for fact, evidence in observation.goal_evidence.items()}})
        try:
            status = supervisor.prepare()
        except Exception as exc:
            reason, error_type = "preparation_error", type(exc).__name__
            break
        for record in supervisor.audit[audit_cursor:]:
            emit({"event": "model_attempt", **record})
        audit_cursor = len(supervisor.audit)
        emit({"event": "decision", "sequence": observation.sequence, "status": status,
              "remaining_goal_facts": list(supervisor.remaining_goals)})
        if status in {"complete", "safe_stop"}:
            reason = status
            break
        if status == "need_observation":
            continue
        try:
            receipt = supervisor.execute_next(session.backend)
        except Exception as exc:
            reason, error_type = "dispatch_error", type(exc).__name__
            break
        emit({"event": "execution", **supervisor.history[-1]})
        if receipt.status == "unknown":
            # The next observation can resolve uncertainty. No blind retry.
            emit({"event": "execution_uncertain", "sequence": observation.sequence})
    result = {"event": "session_summary", "evidence_kind": session.evidence_kind,
              "stop_reason": reason, "supervisor_status": supervisor.status,
              "observations": observations, "model_calls": supervisor.model_calls,
              "commands": len(supervisor.history),
              "confirmed_complete": reason == "complete",
              "remaining_goal_facts": list(supervisor.remaining_goals)}
    # ClientPlanner reports API usage; scripted planners have no token accounting.
    if hasattr(supervisor.planner, "usage"):
        result["model_usage"] = list(supervisor.planner.usage)
    if error_type:
        result["error_type"] = error_type
    emit(result)
    return result


class JsonlJournal:
    """Exclusive-create journal: never overwrite an earlier experimental record."""
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("x", encoding="utf-8")

    def emit(self, event: dict) -> None:
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._file.flush()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._file.close()
