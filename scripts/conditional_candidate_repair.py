"""Frozen archived-candidate replay, planning only; not physical recovery success."""
import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
from ch3.repair.persistent_model_recovery import strict_plan
from ch3.vlm.client import DashScopeVLMClient


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def restore(case, client=None, feedback=False):
    planner = FlexibleFixturePlanner(client, recovery=True, shared={},
        execution_contract=True, error_feedback=feedback)
    request = case['request']
    planner.goals = deepcopy(request['goals'])
    for item in request['current_task_obligations']:
        planner.state.at[item['object_id']] = item['observed_location']
    planner.history = [tuple(x) for x in request['executed_history']]
    planner.set_execution_progress(request['execution_budget']['attempted_transfers'])
    return planner


def freeze(sources, output):
    cases, seen, excluded = [], set(), {}
    for source in sources:
        for path in sorted(source.rglob('summary.json')):
            saved = json.loads(path.read_text())
            for index, audit in enumerate(saved.get('model_audit', [])):
                if audit.get('accepted') or audit.get('phase') == 'initial':
                    continue
                case = dict(source=str(path), audit_index=index, request=audit['request'],
                    content=audit.get('content', ''), image_sha256=audit['image_sha256'])
                try:
                    planner = restore(case)
                    candidate = strict_plan(case['content'], fixed_right_arm=True)
                    _, rejection = planner._evaluate(candidate)
                    if rejection is None:
                        raise ValueError('now_accepted')
                except (ValueError, KeyError, TypeError) as exc:
                    key = type(exc).__name__ + ':' + str(exc)[:100]
                    excluded[key] = excluded.get(key, 0) + 1
                    continue
                image = next((p for p in sorted(path.parent.glob('*.png'))
                    if hashlib.sha256(p.read_bytes()).hexdigest() == case['image_sha256']), None)
                if image is None:
                    raise ValueError(f'missing_archived_image:{path}:{index}')
                case.update(image=str(image), candidate=candidate.model_dump(mode='json'))
                case['case_id'] = digest(dict(snapshot=planner.planning_snapshot(),
                    candidate=case['candidate'], image_sha256=case['image_sha256']))
                if case['case_id'] in seen:
                    continue
                seen.add(case['case_id'])
                rejection['rejected_candidate'] = case['candidate']
                case['rejection'] = rejection
                cases.append(case)
    manifest = dict(scope='conditional_on_archived_rejected_candidate_planning_only',
        inclusion='All parseable noninitial rejected candidates in predeclared v7 sources, still rejected by v8; deduplicate snapshot/candidate/image.',
        sources=[str(p) for p in sources], excluded=excluded, cases=cases,
        extra_calls_per_method=1, temperature=.1, model_seed=0,
        note='Development corpus, not independent natural-failure prevalence or physical execution evidence.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(manifest, stream, indent=2)
    print(json.dumps(dict(frozen_cases=len(cases), excluded=excluded)), flush=True)


def run(manifest_path, output, env_file):
    manifest = json.loads(manifest_path.read_text())
    client = DashScopeVLMClient(env_path=env_file, timeout=60, max_retries=0)
    totals = {'DIRECT_REGENERATION': 0, 'CANDIDATE_ERROR_REVIEW': 0}
    discordant = dict(review_only=0, direct_only=0)
    with output.open('x') as stream:
        for index, case in enumerate(manifest['cases']):
            image = Path(case['image'])
            assert hashlib.sha256(image.read_bytes()).hexdigest() == case['image_sha256']
            results = {}
            for enabled in ((False, True) if index % 2 == 0 else (True, False)):
                name = 'CANDIDATE_ERROR_REVIEW' if enabled else 'DIRECT_REGENERATION'
                planner = restore(case, client, enabled)
                before = deepcopy(planner.state)
                accepted = planner._request(image, feedback=case['rejection']) is not None
                assert planner.state == before
                results[name] = dict(accepted=accepted, audit=planner.audit)
                totals[name] += int(accepted)
            direct = results['DIRECT_REGENERATION']['accepted']
            review = results['CANDIDATE_ERROR_REVIEW']['accepted']
            discordant['review_only'] += int(review and not direct)
            discordant['direct_only'] += int(direct and not review)
            row = dict(case_id=case['case_id'], results=results)
            stream.write(json.dumps(row) + '\n')
            stream.flush()
            print(json.dumps(dict(point=index+1, total=len(manifest['cases']),
                direct=direct, review=review)), flush=True)
    print(json.dumps(dict(record_type='conditional_candidate_repair_summary',
        points=len(manifest['cases']), accepted=totals, discordant=discordant,
        actual_calls=2*len(manifest['cases']), scope=manifest['scope'])), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    if args.source:
        freeze(args.source, args.manifest)
    else:
        if not args.output or not args.env_file:
            parser.error('run requires --output and --env-file')
        run(args.manifest, args.output, args.env_file)


if __name__ == '__main__':
    main()
