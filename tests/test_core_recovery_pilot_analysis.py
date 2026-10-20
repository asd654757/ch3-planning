import json
from scripts.analyze_core_recovery_pilot import analyze, METHODS


def test_missing_duplicate_and_failed_records_not_hidden(tmp_path):
    (tmp_path/'pilot_manifest.json').write_text(json.dumps(dict(seeds=[790],conditions=['medium'])))
    directory=tmp_path/'medium'; directory.mkdir()
    row=dict(seed=790,method=METHODS[0],success=False,error='parse failure',
             runtime_episode_reset_count=1,model_audit=[dict(phase='execution_repair',
             actual_model_calls=1,total_tokens=10,accepted=False,request={'recovery_evidence':{}})])
    (directory/'episodes.jsonl').write_text(json.dumps(row)+'\n')
    result=analyze(tmp_path)
    assert not result['technical_gate_passed']
    assert result['conditions']['medium']['missing']==[(790,METHODS[1])]
    assert result['conditions']['medium']['methods'][METHODS[0]]['failures'][0]['error']=='parse failure'
    (directory/'episodes.jsonl').write_text((json.dumps(row)+'\n')*2)
    assert analyze(tmp_path)['conditions']['medium']['duplicates']==[[790,METHODS[0]]]


def test_complete_records_do_not_auto_authorize_formal(tmp_path):
    (tmp_path/'pilot_manifest.json').write_text(json.dumps(dict(seeds=[790],conditions=['medium'])))
    directory=tmp_path/'medium';directory.mkdir()
    rows=[dict(seed=790,method=m,success=True,runtime_episode_reset_count=1,
               model_audit=[dict(phase='initial',request={'recovery_evidence':{}})]) for m in METHODS]
    (directory/'episodes.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    result=analyze(tmp_path)
    assert result['technical_gate_passed'] and not result['formal_ready']
