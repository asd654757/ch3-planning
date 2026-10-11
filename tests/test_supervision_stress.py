from types import SimpleNamespace
from ch3.supervision.stress_backend import StressBackend
from ch3.supervision.metaworld_adapter import MetaWorldBackend


def test_finite_disturbances(monkeypatch):
    selected = []
    def fake_execute(self, step, **kwargs):
        selected.append(self.perturbation)
        if self.perturbation != 'none':
            self.perturbation_events.append({'type': self.perturbation, 'realized': True})
        return 'receipt'
    monkeypatch.setattr(MetaWorldBackend, 'execute', fake_execute)
    backend = StressBackend(None, 'repeat_slip_place_timeout')
    for skill in ['pick', 'pick', 'pick', 'place', 'place']:
        backend.execute(SimpleNamespace(source_skill=skill))
    assert selected == ['post_grasp_slip', 'post_grasp_slip', 'none', 'place_timeout', 'none']
    assert backend.slips == 2
