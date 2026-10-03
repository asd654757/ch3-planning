from scripts.analyze_model_feedback import analyze


def rows(repair=False):
    return [dict(seed=1,method=m,success=True,model_calls=1,model_audit=(
        [dict(phase='execution_repair',accepted=True,actual_model_calls=1)]
        if repair and m=='MODEL_DIAGNOSTIC_REPAIR' else []))
        for m in ['MODEL_NO_PLAN_REPAIR','MODEL_DIAGNOSTIC_REPAIR']]


def test_high_success_without_repair_does_not_pass_recovery_gate():
    assert not analyze(rows(),[1])['recovery_expansion_gate']['passed']
    assert analyze(rows(True),[1])['recovery_expansion_gate']['passed']


def test_missing_pairs_and_false_visual_completion_block_expansion():
    assert not analyze(rows(True)[:1],[1])['recovery_expansion_gate']['passed']
    data=rows(True)
    data[0].update(success=False,terminal_scores={'object':dict(arrived=False)})
    audit=analyze(data,[1])
    assert audit['online_completion_terminal_goal_mismatches']
    assert not audit['recovery_expansion_gate']['passed']
