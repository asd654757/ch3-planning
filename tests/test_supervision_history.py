import json
import subprocess
import sys


def test_executed_history_and_paired_recovery(tmp_path):
    out=tmp_path/'history.jsonl'
    subprocess.run([sys.executable,'scripts/compare_supervision_history.py','--scripted',
                    '--seeds','1','--output',str(out)],check=True,capture_output=True)
    events=[json.loads(l) for l in out.read_text().splitlines()]
    rows=[e for e in events if e['event']=='case_summary']
    assert len(rows)==9
    assert all(r['confirmed_complete'] and r['prefix_immutable'] for r in rows)
    assert len([e for e in events if e['event']=='prefix_execution'])==27
    for profile in ['lost_held','wrong_held','goal_regression_and_loss']:
        changes=[e for e in events if e['event']=='controlled_disturbance' and e['profile']==profile]
        assert len(changes)==3 and all(e['history_length']==3 for e in changes)
        assert all(e['after']==changes[0]['after'] for e in changes)
    full=[r for r in rows if r['method']=='SUPERVISED_RECOVERY']
    assert all(r['first_recovery_conflicts'] for r in full)
    assert all(not r['first_recovery_conflicts'] for r in rows if r['method']!='SUPERVISED_RECOVERY')
