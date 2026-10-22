import json
from scripts.analyze_supervision_reliability import audit


def write(path, confirmed=True, positive=True):
    ev = [
        {'event':'observation','seed':0,'method':'M','sequence':0,'goal_evidence':{'g':{'value':'false'}}},
        {'event':'model_attempt','seed':0,'method':'M','request':{'observation_sequence':0}},
        {'event':'decision','seed':0,'method':'M','sequence':0,'status':'ready'},
        {'event':'execution','seed':0,'method':'M','observation_sequence':0,'command_id':'x'},
        {'event':'observation','seed':0,'method':'M','sequence':1,'goal_evidence':{'g':{'value':'true' if positive else 'unknown'}}},
        {'event':'decision','seed':0,'method':'M','sequence':1,'status':'complete'},
        {'event':'session_summary','seed':0,'method':'M','confirmed_complete':confirmed},
    ]
    path.write_text('\n'.join(json.dumps(e) for e in ev))


def test_positive_completion_is_audited(tmp_path):
    p=tmp_path/'x.jsonl'; write(p)
    result=audit(p)
    assert result['episodes']==1 and not result['violations']
    assert result['by_method']['M']['positive_completion_evidence']==1


def test_false_completion_is_not_hidden(tmp_path):
    p=tmp_path/'x.jsonl'; write(p, positive=False)
    result=audit(p)
    assert 'complete_without_positive_goal_evidence' in result['violations'][0]['violations']


def test_unknown_receipt_requires_new_observation_before_next_command(tmp_path):
    events = [
        {'event':'observation','seed':0,'method':'M','sequence':0},
        {'event':'decision','seed':0,'method':'M','sequence':0,'status':'ready'},
        {'event':'execution','seed':0,'method':'M','observation_sequence':0,'command_id':'a'},
        {'event':'execution_uncertain','seed':0,'method':'M','sequence':0},
        {'event':'execution','seed':0,'method':'M','observation_sequence':0,'command_id':'b'},
    ]
    p=tmp_path/'unknown.jsonl'
    p.write_text('\n'.join(json.dumps(e) for e in events))
    result=audit(p)
    assert 'unknown_receipt_blind_retry' in result['violations'][0]['violations']


def test_unknown_receipt_followed_by_fresh_observation_is_allowed(tmp_path):
    events = [
        {'event':'observation','seed':0,'method':'M','sequence':0},
        {'event':'decision','seed':0,'method':'M','sequence':0,'status':'ready'},
        {'event':'execution','seed':0,'method':'M','observation_sequence':0,'command_id':'a'},
        {'event':'execution_uncertain','seed':0,'method':'M','sequence':0},
        {'event':'observation','seed':0,'method':'M','sequence':1},
        {'event':'decision','seed':0,'method':'M','sequence':1,'status':'ready'},
        {'event':'execution','seed':0,'method':'M','observation_sequence':1,'command_id':'b'},
    ]
    p=tmp_path/'resolved.jsonl'
    p.write_text('\n'.join(json.dumps(e) for e in events))
    result=audit(p)
    assert not result['violations']
