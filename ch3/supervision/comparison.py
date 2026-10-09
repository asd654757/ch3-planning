"""Matched supervision baselines with shared physical safety validation.

Direct replanning retains identical current-state observations and raw execution
feedback, but omits immutable history and iterative validator diagnostics from
its model request. All methods retain the same dispatch safety gate.
"""
from .core import Supervisor


class NoRecoverySupervisor(Supervisor):
    def prepare(self):
        if (any(h['receipt']['status'] != 'success' for h in self.history) or
                (self._plan is not None and self._observation is not None
                 and self._check(self._plan) is not None)):
            self.status = 'safe_stop'
            return self.status
        return super().prepare()


class DirectReplanSupervisor(Supervisor):
    def _request(self):
        request = super()._request()
        request.pop('executed_history_not_current_state', None)
        request.pop('rejection_feedback', None)
        request.pop('state_conflicts', None)
        request['last_execution_feedback'] = (
            {'action': self.history[-1]['action'], 'receipt': self.history[-1]['receipt']}
            if self.history else None)
        return request


class SharedInitialPlanner:
    """Replay ONLY the common initial output; subsequent calls use real planner."""
    def __init__(self, delegate, initial=None):
        self.delegate = delegate
        self.initial = initial
        self.first = True
        self.generated_initial = None
        self.actual_calls = 0

    def generate(self, request, images):
        if self.first:
            self.first = False
            if self.initial is not None:
                return self.initial
            self.actual_calls += 1
            self.generated_initial = self.delegate.generate(request, images)
            return self.generated_initial
        self.actual_calls += 1
        return self.delegate.generate(request, images)
