from ch3.execution.visual_holding import holding_evidence


def test_comotion_supports_holding():
    result = holding_evidence((100, 100), [(130, 140), (140, 145)], [(130, 130), (140, 135)])
    assert result["status"] == "holding_supported"


def test_stationary_object_near_moving_hand_is_unknown():
    result = holding_evidence((100, 100), [(100, 100), (100, 100)], [(100, 90), (110, 95)])
    assert result["status"] == "unknown"


def test_displaced_object_without_probe_is_unknown():
    assert holding_evidence((100, 100), [(130, 140)], [(130, 130)])["status"] == "unknown"


def test_missing_occluded_or_nonfinite_evidence_is_unknown():
    for target in (None, (float("nan"), 100)):
        assert holding_evidence((100, 100), [(100, 100), target], [(100, 90), (110, 90)])["status"] == "unknown"


def test_distant_comoving_color_is_not_holding():
    assert holding_evidence((100, 100), [(130, 140), (140, 145)], [(230, 130), (240, 135)])["status"] == "unknown"
