from ch3.execution.multiview_evidence import holding_from_views


def test_positive_view_and_occluded_view_are_not_mixed():
    initial={'corner':[0,0],'topview':None}
    records=[{'views':{'corner':{'target_pixel':[10,0],'hand_pixel':[11,0]},
                       'topview':{'target_pixel':None,'hand_pixel':[500,500]}}},
             {'views':{'corner':{'target_pixel':[15,0],'hand_pixel':[16,0]},
                       'topview':{'target_pixel':None,'hand_pixel':[0,0]}}}]
    result=holding_from_views(initial,records)
    assert result['status']=='holding_supported'
    assert result['supporting_cameras']==['corner']
    assert result['per_camera']['topview']['status']=='unknown'


def test_visibility_without_comotion_never_supports_holding():
    records=[{'views':{'corner':{'target_pixel':[0,0],'hand_pixel':[1,0]}}},
             {'views':{'corner':{'target_pixel':[0,0],'hand_pixel':[20,0]}}}]
    assert holding_from_views({'corner':[0,0]},records)['status']=='unknown'


def test_execution_scripts_compile():
    from pathlib import Path
    for filename in ['scripts/persistent_reference_sequence.py',
        'scripts/flexible_fixture_pilot.py','scripts/check_goal_observation_backend.py']:
        source=Path(filename).read_text()
        compile(source,filename,'exec')
