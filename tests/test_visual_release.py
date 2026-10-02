import numpy as np
import pytest
from ch3.execution.visual_release import release_evidence
from ch3.execution.visual_holding import color_pixel


def evidence(**kwargs):
    args = dict(target_pixels=[(100, 100), (101, 100)], hand_pixels=[(100, 60), (110, 60)],
                target_world_xy=(0, .7), destination_xy=(0, .7),
                prior_holding_supported=True, open_retreat_completed=True, single_object_fixture=True)
    args.update(kwargs)
    return release_evidence(**args)


def test_supported_release_requires_independent_evidence():
    assert evidence()["status"] == "release_supported"
    for flag in ("prior_holding_supported", "open_retreat_completed", "single_object_fixture"):
        assert not evidence(**{flag: False})["empty_hand_supported"]
    assert evidence(target_pixels=[None, (1, 2)])["status"] == "unknown"
    assert evidence(target_pixels=[(100, 100), (110, 100)])["status"] == "unknown"
    assert evidence(hand_pixels=[(100, 100), (110, 100)])["status"] == "unknown"
    assert evidence(destination_xy=(.1, .7))["status"] == "unknown"


@pytest.mark.parametrize("color,rgb", [("yellow", (230, 190, 10)), ("magenta", (200, 10, 200))])
def test_locator_rejects_missing_and_multiple_candidates(color, rgb):
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        color_pixel(image, color, require_unique=True)
    image[2:8, 2:8] = rgb
    assert color_pixel(image, color, require_unique=True)[0] == (4.5, 4.5)
    image[20:26, 20:26] = rgb
    with pytest.raises(ValueError, match="ambiguous"):
        color_pixel(image, color, require_unique=True)
