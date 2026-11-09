from ch3.execution.recovery_contact_evidence import aperture_evidence, regrasp_gate


def samples(distance=.03):
    return [{"jaw_body_distance_m":distance,"hand_z_m":.18,"commanded_gripper":1} for _ in range(5)]


def test_aperture_is_only_empty_evidence_not_holding_or_retry_permission():
    empty=aperture_evidence(samples())
    assert empty["closure_supported"] and not empty["empty_supported"] and not empty["holding_supported"]
    assert not regrasp_gate(empty,{"status":"unknown"},target_visible=True,observation_id="new")["authorized"]


def test_blocked_jaws_and_open_low_or_unstable_samples_stay_unknown():
    assert not aperture_evidence(samples(.048))["empty_supported"]
    for key,value in (("hand_z_m",.05),("commanded_gripper",-1),("jaw_body_distance_m",float("nan"))):
        records=samples(); records[-1][key]=value
        assert not aperture_evidence(records)["empty_supported"]


def test_joint_gate_requires_closed_fixture_scope_and_no_positive_contradiction():
    empty=aperture_evidence(samples())
    negative={"status":"target_not_following_supported"}
    assert not regrasp_gate(empty,negative,target_visible=True,observation_id="new")["authorized"]
    args=dict(target_visible=True,observation_id="new",sole_grasp_candidate="blue_candidate",initial_empty_known=True)
    assert regrasp_gate(empty,negative,**args)["authorized"]
    assert not regrasp_gate(empty,negative,holding={"status":"holding_supported"},**args)["authorized"]
    assert not regrasp_gate(empty,negative,target_visible=False,observation_id="new")["authorized"]
