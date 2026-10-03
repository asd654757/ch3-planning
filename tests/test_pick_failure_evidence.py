from ch3.execution.pick_failure_evidence import failure_scope


def test_trace_never_assumes_empty_after_closure():
    for phase in range(4):
        result = dict(reason='waypoint_timeout', trace=[dict(phase=i, steps=10) for i in range(phase+1)])
        assert failure_scope(result) == ('open_only' if phase<2 else 'closure_attempted')
    assert failure_scope(dict(reason='episode_ended', trace=[])) == 'unknown'
    assert failure_scope(dict(reason='waypoint_timeout', trace=[dict(phase=2, steps=10)])) == 'unknown'
