"""Final attribution variants; frozen V3 implementation is not modified."""
from .core import Supervisor
from .comparison import DirectReplanSupervisor


class WithoutConflictSupervisor(Supervisor):
    """Remove ONLY explicit conflict diagnostics, retain history/checker feedback."""
    def _request(self):
        request = super()._request()
        request.pop('state_conflicts', None)
        return request


class StateFirstDirectSupervisor(DirectReplanSupervisor):
    """Same raw feedback, labeled as historical; no diagnostic or checker hints."""
    def _request(self):
        request = super()._request()
        receipt = request.pop('last_execution_feedback')
        request['historical_execution_event_not_current_state'] = receipt
        request['planning_basis'] = {
            'current_occupancy': request['held_objects'],
            'current_facts': request['current_observed_facts'],
            'rule': 'Plan from this observation only. A historical success event cannot override current occupancy.'}
        return request
