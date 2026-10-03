from ch3.execution.observed_goal import observed_goal
from ch3.execution.visual_release import release_evidence


def test_observed_goal_does_not_use_commanded_wrong_center():
    assert observed_goal([[.0298,.84],[.0298,.84]], [0,.84])['status']=='not_satisfied'
    assert observed_goal([[.001,.84],[.002,.84]], [0,.84])['status']=='satisfied'


def test_unknown_boundary_disagreement_and_missing_observations():
    assert observed_goal([[.024,.84],[.026,.84]], [0,.84])['status']=='unknown'
    assert observed_goal([], [0,.84])['status']=='unknown'
    assert observed_goal([[float('nan'),.84],[0,.84]], [0,.84])['status']=='unknown'
    assert observed_goal([[0,.84],[.01,.84]], [0,.84])['status']=='unknown'


def test_invalid_layout_range_rejected_before_simulator_start(tmp_path):
    import pytest
    from ch3.execution.multiobject_scene import make_scene
    for spread in [-.01, .081, float('nan')]:
        with pytest.raises(ValueError, match='spread'):
            make_scene(tmp_path/'nonexistent.xml', seed=0, initial_y_spread=spread)


def test_detachment_does_not_require_goal_completion_in_v2():
    args=dict(target_pixels=[(100,100),(101,100)], hand_pixels=[(100,60),(110,60)],
        target_world_xy=(.1,.7), destination_xy=(0,.7), prior_holding_supported=True,
        open_retreat_completed=True, single_object_fixture=True)
    assert release_evidence(**args)['status']=='unknown'
    result=release_evidence(**args, require_destination=False)
    assert result['status']=='release_supported' and not result['destination_supported']
