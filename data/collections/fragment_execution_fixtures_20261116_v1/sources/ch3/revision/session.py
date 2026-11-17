"""Revision transaction state: unresolved requests suspend execution."""
from dataclasses import replace

from ch3.revision.history import RevisionResult, apply_patch, decide


class RevisionSession:
    def __init__(self, snapshot, horizon=8):
        self.active = snapshot.validate()
        self.pending = None
        self.audit = []
        self.horizon = horizon

    @property
    def working_snapshot(self):
        if self.pending is None:
            return self.active
        # A follow-up revision edits the pending intent, including its conflicts.
        return replace(self.active, contract=self.pending.proposed)

    @property
    def executable_contract(self):
        if self.pending is not None:
            raise ValueError("unresolved revision suspends execution of the old task")
        # Even the initial accepted task must be currently feasible in this model.
        if decide(self.active, self.active.contract, self.horizon).status != "ready":
            raise ValueError("active task is not currently executable")
        return self.active.contract

    def submit(self, patch, instruction):
        base = self.working_snapshot
        proposed = apply_patch(base, patch, instruction)
        result = RevisionResult(proposed, decide(base, proposed, self.horizon))
        self.audit.append((base.digest, instruction, patch, result))
        if result.executable:
            self.active = replace(base, contract=proposed)
            self.pending = None
        else:
            self.pending = result
        return result
