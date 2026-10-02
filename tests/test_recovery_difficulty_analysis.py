import json
from scripts.analyze_recovery_difficulty import analyze


def test_incomplete_is_not_complete(tmp_path):
    source=tmp_path/'rows.jsonl';source.write_text('')
    manifest=tmp_path/'cases.jsonl';manifest.write_text(json.dumps({'case_id':'a'})+'\n')
    report=analyze(source,manifest)
    assert not report['complete']
    assert 'missing_extra_or_duplicate_records' in report['audit_errors']
