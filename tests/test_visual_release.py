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


def test_adjacent_occluded_color_patches_are_grouped_but_distant_are_not():
    image = np.zeros((50, 50, 3), dtype=np.uint8)
    image[5:15, 5:10] = (10, 20, 230)
    image[5:15, 12:17] = (10, 20, 230)
    assert color_pixel(image, "blue", require_unique=True)[1] == 100
    image[30:40, 30:40] = (10, 20, 230)
    with pytest.raises(ValueError, match="ambiguous"):
        color_pixel(image, "blue", require_unique=True)


def test_region_bbox_center_is_explicit_statistic_not_default():
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    image[5:25, 5:25] = (200, 10, 200)
    image[10:25, 10:20] = 0  # controlled occlusion, outer edges still visible
    center, _ = color_pixel(image, "magenta", require_unique=True, pixel_statistic="bbox_center")
    assert center == (14.5, 14.5)
    with pytest.raises(ValueError):
        color_pixel(image, "magenta", pixel_statistic="truth")
