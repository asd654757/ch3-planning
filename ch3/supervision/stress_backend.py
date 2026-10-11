"""Finite repeated physical disturbances; frozen backend implementation unchanged."""
from .metaworld_adapter import MetaWorldBackend


class StressBackend(MetaWorldBackend):
    def __init__(self, executor, profile):
        super().__init__(executor, max_steps=300)
        self.profile = profile
        self.slips = 0
        self.place_interrupted = False

    def execute(self, step, **kwargs):
        self.perturbation = 'none'
        self.perturbation_used = False
        if step.source_skill == 'pick' and self.slips < 2:
            self.perturbation = 'post_grasp_slip'
        elif (step.source_skill == 'place' and self.profile == 'repeat_slip_place_timeout'
              and not self.place_interrupted):
            self.perturbation = 'place_timeout'
            self.place_interrupted = True
        before = len(self.perturbation_events)
        receipt = super().execute(step, **kwargs)
        self.slips += sum(e['type'] == 'post_grasp_slip'
                          for e in self.perturbation_events[before:])
        return receipt
