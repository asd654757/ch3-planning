"""Audit execution-feedback safety invariants in supervision JSONL journals.

This is a protocol audit, not a success-rate comparison. It never uses simulator
truth beyond the evidence fields already recorded in the journal.
"""
import argparse
import json
from collections import Counter
from pathlib import Path


def audit(path):
    events = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    cases = {}
    for event in events:
        key = (event.get('seed'), event.get('method'))
        if event.get('event') in {'session_start', 'observation', 'model_attempt',
                                  'decision', 'execution', 'execution_uncertain', 'session_summary'}:
            cases.setdefault(key, []).append(event)
    violations = []
    totals = Counter()
    by_method = {}
    for key, rows in cases.items():
        seed, method = key
        observations = [e for e in rows if e['event'] == 'observation']
        decisions = [e for e in rows if e['event'] == 'decision']
        executions = [e for e in rows if e['event'] == 'execution']
        attempts = [e for e in rows if e['event'] == 'model_attempt']
        summary = next((e for e in rows if e['event'] == 'session_summary'), None)
        local = []
        obs_by_seq = {e['sequence']: e for e in observations}
        if len(obs_by_seq) != len(observations):
            local.append('duplicate_observation_sequence')
        command_ids = [e['command_id'] for e in executions]
        if len(set(command_ids)) != len(command_ids):
            local.append('duplicate_command_id')
        for e in executions:
            seq = e['observation_sequence']
            if seq not in obs_by_seq:
                local.append('execution_without_observation')
            matching = [d for d in decisions if d['sequence'] == seq and d['status'] == 'ready']
            if not matching:
                local.append('execution_without_ready_decision')
        for e in attempts:
            seq = e['request'].get('observation_sequence')
            if seq not in obs_by_seq:
                local.append('model_request_without_observation')
        if summary and summary.get('confirmed_complete'):
            last_obs = max(observations, key=lambda e: e['sequence']) if observations else None
            evidence = (last_obs or {}).get('goal_evidence', {})
            if not evidence or not all(v.get('value') == 'true' for v in evidence.values()):
                local.append('complete_without_positive_goal_evidence')
            if not any(d['status'] == 'complete' for d in decisions):
                local.append('complete_without_complete_decision')
        if any(e['event'] == 'execution_uncertain' for e in rows):
            totals['uncertain_execution_events'] += sum(e['event'] == 'execution_uncertain' for e in rows)
        totals['episodes'] += 1
        totals['violating_episodes'] += bool(local)
        for item in local:
            totals[item] += 1
        by_method.setdefault(str(method), dict(episodes=0, violating_episodes=0,
            confirmed_complete=0, commands=0, uncertain_execution_events=0,
            positive_completion_evidence=0, violations=Counter()))
        m = by_method[str(method)]
        m['episodes'] += 1
        m['violating_episodes'] += bool(local)
        m['confirmed_complete'] += bool(summary and summary.get('confirmed_complete'))
        m['commands'] += len(executions)
        m['uncertain_execution_events'] += sum(e['event'] == 'execution_uncertain' for e in rows)
        if summary and summary.get('confirmed_complete'):
            last_obs = max(observations, key=lambda e: e['sequence']) if observations else {}
            m['positive_completion_evidence'] += all(v.get('value') == 'true' for v in last_obs.get('goal_evidence', {}).values())
        m['violations'].update(local)
        if local:
            violations.append(dict(seed=seed, method=method, violations=local))
    for m in by_method.values():
        m['violations'] = dict(m['violations'])
    return dict(journal=str(path), episodes=totals['episodes'], violations=violations,
        totals=dict(totals), by_method=by_method,
        interpretation='Protocol audit only; absence of violations does not prove task success, superiority, equivalence, or open-world capability.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('journal', type=Path)
    print(json.dumps(audit(p.parse_args().journal), ensure_ascii=False, indent=2))
