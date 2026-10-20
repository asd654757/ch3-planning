import json
from scripts.analyze_occlusion_pilot import analyze


def test_missing_and_failed_rows_never_authorize_formal_run(tmp_path):
    (tmp_path/'protocol.json').write_text(json.dumps({'seeds':[750]}))
    row={'seed':750,'method':'MODEL_DIRECT_REPLAN','success':False,
        'error':'holding_unknown','model_audit':[], 'episode_resets':1}
    (tmp_path/'episodes.jsonl').write_text(json.dumps(row)+'\n')
    result=analyze(tmp_path)
    assert not result['complete'] and not result['formal_ready']
    assert result['methods']['MODEL_DIRECT_REPLAN']['errors']=={'holding_unknown':1}
    assert result['missing']==[(750,'MODEL_COMPACT_REPAIR')]
