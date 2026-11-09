import random

import pytest

from ch3.repair.certificate_recovery import CertificateRecoveryPlanner, EvidenceContract
from ch3.repair.recovery_decision import ActionModel, Branch, Budget, RecoveryModel


def problem():
    # Same physical pose may have a current observation or an invalidated one.
    # A controller acknowledgement cannot replace a verification observation.
    actions={
        "observe":ActionModel(Budget(1,0,0,1),0,{
            "at_pose":(Branch(1,"rgb_confirmed",next_state="at_pose"),)}),
        "continue":ActionModel(Budget(0,1,0,1),0,{
            "at_pose":(Branch(1,"done",terminal="success"),)}),
        "retry":ActionModel(Budget(0,1,0,1),0,{
            "at_pose":(Branch(1,"reset_acknowledged",next_state="at_pose"),)}),
    }
    model=RecoveryModel(actions,{"at_pose":0},"synthetic_contract_test","zero_boundary")
    contracts={"observe":EvidenceContract(produces={"rgb_confirmed":frozenset({"holding"})}),
               "continue":EvidenceContract(requires=frozenset({"holding"})),
               "retry":EvidenceContract(invalidates=frozenset({"holding"}))}
    return model,contracts


def test_identical_physical_belief_needs_different_decision_when_evidence_is_stale():
    model,contracts=problem()
    planner=CertificateRecoveryPlanner(model,contracts,depth=2)
    budget=Budget(1,2,0,2)
    assert planner.choose({"at_pose":1}, {"holding"},budget).action=="continue"
    decision=planner.choose({"at_pose":1},set(),budget)
    assert decision.action=="observe"
    assert "continue" not in decision.action_values


def test_reset_acknowledgement_does_not_regenerate_invalidated_evidence():
    _,contracts=problem()
    assert contracts["retry"].update(frozenset({"holding"}),"reset_acknowledged")==frozenset()


def test_slicing_matches_full_state_dp_and_uncached_oracle():
    model,contracts=problem()
    randomizer=random.Random(19)
    for _ in range(30):
        evidence={f"unused:{i}" for i in range(8) if randomizer.random()<.5}
        if randomizer.random()<.5:
            evidence.add("holding")
        budget=Budget(randomizer.randrange(3),randomizer.randrange(1,4),0,randomizer.randrange(1,6))
        results=[CertificateRecoveryPlanner(model,contracts,depth=4,slice_evidence=s,memoize=m)
                 .choose({"at_pose":1.},evidence,budget) for s,m in ((True,True),(False,True),(False,False))]
        assert results[0]==results[1]==results[2]


def test_zero_mass_unsupported_hypothesis_is_not_used_and_nonzero_is_rejected():
    model,contracts=problem()
    planner=CertificateRecoveryPlanner(model,contracts)
    budget=Budget(1,1,0,4)
    assert planner.choose({"at_pose":1.,"other":0.},set(),budget).value==1
    assert planner.choose({"at_pose":.5,"other":.5},set(),budget).value==0


def test_unmodelled_contract_is_not_silently_generated():
    model,_=problem()
    with pytest.raises(ValueError,match="contract"):
        CertificateRecoveryPlanner(model,{})


def enumerated_oracle(model, contracts, belief, evidence, budget, depth):
    """Independent uncompressed observation tree; never branch on hidden state."""
    values = {"stop": 0.}
    if not depth:
        return values
    for name, spec in model.actions.items():
        rule = contracts[name]
        if not rule.requires.issubset(evidence) or budget.spend(spec.resources) is None:
            continue
        if any(p > 0 and s not in spec.transitions for s, p in belief.items()):
            continue
        outcomes = {}
        total = 0.
        for state, prior in belief.items():
            if prior == 0:
                continue
            for branch in spec.transitions[state]:
                probability = prior*branch.probability
                if branch.terminal is not None:
                    total += probability*(branch.terminal == "success")
                    continue
                cost = branch.resources if branch.resources is not None else spec.resources
                key = (branch.observation, cost)
                outcomes.setdefault(key, []).append((probability, branch.next_state))
        for (label, cost), paths in outcomes.items():
            probability = sum(p for p, _ in paths)
            posterior = {}
            for p, state in paths:
                posterior[state] = posterior.get(state, 0.) + p/probability
            updated = (set(evidence)-set(rule.invalidates)) | set(rule.produces.get(label, ()))
            future = enumerated_oracle(model, contracts, posterior, updated,
                                       budget.spend(cost), depth-1)
            total += probability*max(future.values())
        values[name] = total
    return values


def test_random_stochastic_models_match_independent_uncompressed_observation_tree():
    rng = random.Random(20261109)
    tokens = ["holding", "empty", "target_bound", "old_binding"]
    def subset():
        return frozenset(t for t in tokens if rng.random() < .35)
    for _ in range(100):
        actions, contracts = {}, {}
        for name in ("observe", "continue", "retry", "replan"):
            cap = Budget(1 if name == "observe" else 0, 0 if name == "observe" else 1, 0, 2)
            transitions = {}
            for state in ("held", "released"):
                if name == "continue":
                    transitions[state] = (Branch(.25, "completed", terminal="success"),
                                          Branch(.75, "failed", terminal="failure"))
                else:
                    # Identical sensor label, distinct actual durations. Cost
                    # is observable; latent state is not supplied to a policy.
                    transitions[state] = (
                        Branch(.25, "sample", next_state="held", resources=Budget(cap.observations, cap.commands, 0, 1)),
                        Branch(.25, "sample", next_state="released", resources=Budget(cap.observations, cap.commands, 0, 1)),
                        Branch(.5, "sample", next_state=state, resources=cap))
            actions[name] = ActionModel(cap, 0, transitions)
            contracts[name] = EvidenceContract(subset(), subset(), {"sample": subset()})
        model = RecoveryModel(actions, {"held": 0, "released": 0}, "randomized_test", "zero")
        belief = {"held": .25, "released": .75}
        evidence = subset()
        budget = Budget(rng.randrange(4), rng.randrange(4), 0, rng.randrange(1, 7))
        depth = rng.randrange(1, 5)
        expected = enumerated_oracle(model, contracts, belief, evidence, budget, depth)
        for sliced in (False, True):
            decision = CertificateRecoveryPlanner(model, contracts, depth=depth,
                                                  slice_evidence=sliced).choose(belief, evidence, budget)
            assert decision.action_values == pytest.approx(expected)
            assert decision.value == pytest.approx(max(expected.values()))


def test_information_gathering_is_not_clairvoyant_on_identical_observations():
    model = RecoveryModel({
        "observe": ActionModel(Budget(1, 0, 0, 1), 0, {
            s: (Branch(1, "ambiguous", next_state=s),) for s in ("left", "right")}),
        "continue": ActionModel(Budget(0, 1, 0, 1), 0, {
            "left": (Branch(1, "done", terminal="success"),),
            "right": (Branch(1, "failed", terminal="failure"),)}),
        "retry": ActionModel(Budget(0, 1, 0, 1), 0, {
            "right": (Branch(1, "done", terminal="success"),),
            "left": (Branch(1, "failed", terminal="failure"),)})},
        {"left": 0, "right": 0}, "aliasing_test", "zero")
    contracts = {name: EvidenceContract() for name in model.actions}
    decision = CertificateRecoveryPlanner(model, contracts, depth=2).choose(
        {"left": .5, "right": .5}, set(), Budget(1, 1, 0, 2))
    assert decision.value == .5
    assert decision.action_values["observe"] == .5  # not oracle's 1.0
