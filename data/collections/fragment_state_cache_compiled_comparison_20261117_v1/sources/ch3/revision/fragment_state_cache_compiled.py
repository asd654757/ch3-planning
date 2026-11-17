"""State-bound control compiled through the exact same matcher as full guards."""
from ch3.revision.fragment_state_cache import StateBoundBank


class CompiledStateBoundBank(StateBoundBank):
    def __init__(self, domain, fixed_bank, failures, oracle=None):
        super().__init__(domain, fixed_bank, failures, oracle)
        self.references = {}
        candidates = [list(entries) for entries in fixed_bank.conditions]
        for index, entries in enumerate(fixed_bank.conditions):
            for bits in entries:
                self.references[index, bits] = fixed_bank.references[index, bits]
        for (index, bits), proof in self.entries.items():
            candidates[index].append(bits)
            self.references.setdefault((index, bits), {k: v for k, v in proof.items()
                if k not in ("support", "state_index")})
        self.conditions = []
        for entries in candidates:
            reduced = []
            for bits in sorted(set(entries), key=lambda b: (b.bit_count(), b)):
                if not any(previous & bits == previous for previous in reduced):
                    reduced.append(bits)
            self.conditions.append(tuple(reduced))
        # Includes the same token check, ONE feature extraction, subsumption,
        # and evidence construction. No duplicate validation on control misses.
        self.evidence = domain.regions.RegionBank.evidence.__get__(self, type(self))
