"""Append a supported finding about an unknown event without replacing its record."""
from dataclasses import dataclass, replace
from typing import Literal

from ch3.revision.history import Event
from ch3.revision.runtime import RevisionRuntime


@dataclass(frozen=True)
class EvidenceFinding:
    """Input from the registered observation adapter, not a planner assertion."""

    event_seq: int
    verdict: Literal["confirmed", "refuted"]
    observation_id: str
    snapshot_digest: str
    generation: int


@dataclass(frozen=True)
class RecordedFinding:
    event: Event
    finding: EvidenceFinding


class EvidenceResolution:
    """Extend either original or certificate-backed RevisionRuntime by inheritance.

    The observation adapter is responsible for the quality and provenance of a
    finding. This interface enforces binding, event identity and finality.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._findings = ()

    @property
    def findings(self):
        return self._findings

    @property
    def original_history(self):
        originals = {entry.event.seq: entry.event for entry in self._findings}
        return tuple(originals.get(event.seq, event) for event in self.snapshot.history)

    def resolve_evidence(self, finding: EvidenceFinding):
        """Commit a finding and invalidate dispatch before any reassessment."""
        previous = self.snapshot
        if (type(finding.generation) is not int or finding.generation != self.generation
                or finding.snapshot_digest != previous.digest):
            raise ValueError("stale evidence finding")
        if finding.verdict not in ("confirmed", "refuted") or type(finding.event_seq) is not int:
            raise ValueError("invalid evidence finding")
        used_ids = {event.observation_id for event in previous.history}
        used_ids.add(previous.observation_id)
        used_ids.update(entry.finding.observation_id for entry in self._findings)
        if (not isinstance(finding.observation_id, str) or not finding.observation_id.strip()
                or finding.observation_id in used_ids):
            raise ValueError("fresh supporting observation required")
        event = next((event for event in previous.history if event.seq == finding.event_seq), None)
        if event is None or event.evidence != "unknown":
            raise ValueError("finding must identify an unresolved historical event")

        effective_history = tuple(replace(item, evidence=finding.verdict) if item.seq == event.seq else item
                                  for item in previous.history)
        current = replace(previous, history=effective_history, observation_id=finding.observation_id).validate()
        self._findings = (*self._findings, RecordedFinding(event, finding))
        self.snapshot = current
        self.generation += 1
        self._ticket = None
        self.decision = None
        self.pending = True
        return current


class EvidenceRevisionRuntime(EvidenceResolution, RevisionRuntime):
    """RevisionRuntime with explicit late findings from its observation adapter."""
