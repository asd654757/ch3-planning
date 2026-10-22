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


def test_paired_analysis_reports_discordance_costs_and_small_sample_warning():
    from scripts.analyze_core_recovery_pilot import paired_summary

    rows = [
        dict(seed=1, method=METHODS[0], success=False,
             model_audit=[dict(phase='execution_repair', actual_model_calls=2,
                               total_tokens=100)],
             events=[dict(execution=dict(steps=10))]),
        dict(seed=1, method=METHODS[1], success=True,
             model_audit=[dict(phase='execution_repair', actual_model_calls=1,
                               total_tokens=120)],
             events=[dict(execution=dict(steps=8))]),
        dict(seed=2, method=METHODS[0], success=True,
             model_audit=[dict(phase='execution_repair', actual_model_calls=1,
                               total_tokens=80)],
             events=[dict(execution=dict(steps=9))]),
        dict(seed=2, method=METHODS[1], success=True,
             model_audit=[dict(phase='execution_repair', actual_model_calls=1,
                               total_tokens=70)],
             events=[dict(execution=dict(steps=9))]),
    ]
    result = paired_summary(rows)
    assert result['paired_n'] == 2
    assert result['success_pairs'] == dict(direct_only_success=0,
        compact_only_success=1, discordant_pairs=1, exact_two_sided_p=1.0)
    assert result['costs']['recovery_calls']['compact_minus_direct'] == [-1, 0]
    assert result['costs']['recovery_tokens']['compact_minus_direct'] == [20, -10]
    assert result['costs']['physical_control_steps']['compact_minus_direct'] == [-2, 0]
    assert result['inferential_status'] == 'descriptive_only_small_paired_sample'


def test_paired_analysis_excludes_duplicate_method_records_instead_of_overwriting():
    from scripts.analyze_core_recovery_pilot import paired_summary

    rows = [
        dict(seed=1, method=METHODS[0], success=True),
        dict(seed=1, method=METHODS[0], success=False),
        dict(seed=1, method=METHODS[1], success=True),
        dict(seed=2, method=METHODS[0], success=True),
        dict(seed=2, method=METHODS[1], success=False),
    ]
    result = paired_summary(rows)
    assert result['paired_seeds'] == [2]
    assert result['paired_n'] == 1
    assert result['duplicate_keys'] == [[1, METHODS[0]]]
    assert result['excluded_duplicate_seeds'] == [1]
