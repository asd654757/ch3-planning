import pytest

from ch3.execution.visual_holding import failed_pick_evidence


def test_stationary_target_is_not_an_empty_hand_fact():
    result = failed_pick_evidence((100, 100), [(100, 100), (100, 100)], [(100, 120), (110, 120)])
    assert result["status"] == "target_not_following_supported"
    assert not result["empty_hand_supported"]
    assert not result["retry_authorized"]
    assert result["observed_facts"] == []


@pytest.mark.parametrize("targets,hands", [
    ([(130, 140), (140, 145)], [(130, 130), (140, 135)]),
    ([(100, 100), (100, 100)], [(100, 102), (110, 102)]),
    ([(100, 100), (100, 100)], [(100, 120), (101, 120)]),
    ([(100, 100), (110, 100), (100, 100)], [(100, 120), (110, 120), (120, 120)]),
    ([(100, 100), None], [(100, 120), (110, 120)]),
    ([(100, 100), (float("nan"), 100)], [(100, 120), (110, 120)]),
])
def test_positive_missing_ambiguous_or_moving_evidence_stays_unknown(targets, hands):
    result = failed_pick_evidence((100, 100), targets, hands)
    assert result["status"] == "unknown"
    assert not result["retry_authorized"]


def test_single_frame_is_insufficient():
    assert failed_pick_evidence((100, 100), [(100, 100)], [(100, 120)])["status"] == "unknown"
