"""Offline closure evidence. No LLM calls; mechanism results are interface tests."""
import argparse
import ast
import json
from pathlib import Path

from ch3.capability.registry import CapabilityRegistry
from ch3.state.world_state import WorldState
from ch3.supervision import Evidence, Observation, Supervisor, Task, Truth
from ch3.validator.pipeline import Validator
from ch3.repair.flexible_fixture_planner import FlexibleFixturePlanner
from scripts.analyze_core_recovery_pilot import analyze

GOAL = 'on(red_cube_0, tray_0)'


class NoCallsPlanner:
    def generate(self, request, images):
        raise AssertionError('These boundary cases must not need a model')


class WithoutPositiveEvidence(Supervisor):
    """Test-only single-mechanism ablation: trust state instead of goal evidence."""
    @property
    def remaining_goals(self):
        facts = self._observation.state.facts() if self._observation else set()
        return tuple(g for g in self.task.goal_facts if g not in facts)


def mechanism_check():
    registry = CapabilityRegistry.from_yaml('config/capability_registry.yaml')
    objects = {'red_cube_0', 'blue_cube_0', 'tray_0', 'box_0'}
    rows = []
    # Same observation/state for both methods. Labels never reach either controller.
    for condition, value in [('missing_evidence', None), ('unknown_evidence', Truth.UNKNOWN),
                             ('positive_evidence_control', Truth.TRUE)]:
        for method, cls in [('FULL', Supervisor), ('WITHOUT_POSITIVE_EVIDENCE', WithoutPositiveEvidence)]:
            state = WorldState.table_scene(objects)
            state.at['red_cube_0'] = 'tray_0'
            evidence = {} if value is None else {GOAL: Evidence(value, 'test_observer')}
            supervisor = cls(Task('Move red to tray', (GOAL,)), Validator(objects, registry), NoCallsPlanner())
            supervisor.observe(Observation('fixture', 0, state, {'left': None, 'right': None}, evidence))
            status = supervisor.prepare()
            rows.append(dict(condition=condition, method=method, status=status,
                model_calls=supervisor.model_calls,
                unsupported_completion=status == 'complete' and value != Truth.TRUE))
    return dict(evidence_kind='controlled_interface_ablation_not_physical_or_model_performance',
        changed_mechanism='positive goal evidence requirement only', cases=rows,
        interpretation='Demonstrates evidence-gate necessity by construction under incomplete evidence; '
                       'not an estimate of real observation errors or robot success improvement.')


FORBIDDEN = {'terminal_scores', 'protected_displacement_samples_m', 'protected_object_preserved',
             'scoring_truth', 'ground_truth', 'terminal_success', 'runtime_episode_reset_count'}


def forbidden_paths(value, path='request'):
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN:
                result.append(path + '.' + key)
            result.extend(forbidden_paths(child, path + '.' + key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            result.extend(forbidden_paths(child, f'{path}[{i}]'))
    return result


def isolation_check(root):
    tree = ast.parse(Path('scripts/persistent_reference_sequence.py').read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    body = next(n.body for n in ast.walk(run) if isinstance(n, ast.Try))
    score_start = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == 'scores' for t in n.targets))
    scoring_nodes = body[score_start:]
    calls_after_score = [ast.unparse(n.func) for node in scoring_nodes for n in ast.walk(node)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == 'model_planner']
    violations = []
    requests = 0
    terminal_scored = 0
    for path in sorted(root.glob('*/episodes.jsonl')):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            terminal_scored += 'terminal_scores' in row
            for index, attempt in enumerate(row.get('model_audit', [])):
                requests += 1
                for found in forbidden_paths(attempt.get('request', {})):
                    violations.append(dict(source=str(path), seed=row['seed'], method=row['method'],
                                           attempt=index, field=found))
    # Causal canary: changing an attached scoring result must not change model snapshot.
    planner = FlexibleFixturePlanner(None, recovery=True, shared={}, execution_contract=True)
    planner.terminal_scores = {'sentinel': 'CANARY_A'}
    before = planner.planning_snapshot()
    planner.terminal_scores = {'sentinel': 'CANARY_B'}
    canary_unchanged = before == planner.planning_snapshot()
    return dict(requests_checked=requests, scored_episodes=terminal_scored,
        forbidden_request_fields=violations, planner_calls_after_terminal_scoring=calls_after_score,
        scoring_canary_snapshot_unchanged=canary_unchanged,
        passed=requests > 0 and not violations and not calls_after_score and canary_unchanged,
        limitations=['Field/AST/canary checks are bounded audits, not a general information-flow proof.',
            'Known fixture, initial symbolic locations, color labels, camera calibration and target planes '
            'remain planner/controller priors; this is not blind or open-world perception.',
            'Forbidden-key checks do not establish the correctness of RGB-derived evidence.'])


def closeout(root, output):
    output.mkdir(parents=True, exist_ok=True)
    results = analyze(root)
    mechanism = mechanism_check()
    isolation = isolation_check(root)
    (output/'analysis.json').write_text(json.dumps(results, indent=2))
    (output/'mechanism.json').write_text(json.dumps(mechanism, indent=2))
    (output/'isolation.json').write_text(json.dumps(isolation, indent=2))
    lines = ['# Reliable recovery frozen assessment', '',
        'Labels: direct = MODEL_DIRECT_REPLAN; full implementation = MODEL_COMPACT_REPAIR. '
        'This comparison retains shared execution guards and does not isolate their contribution.', '',
        f"Complete data gate: {results['technical_gate_passed']}. Information boundary audit: {isolation['passed']}.", '',
        '| Condition | Method | Success/episodes | Recovery API calls | Recovery tokens | Candidate rejects |',
        '|---|---|---:|---:|---:|---:|']
    for condition, result in results['conditions'].items():
        for method, values in result['methods'].items():
            lines.append(f"| {condition} | {method} | {values['successes']}/{values['episodes']} | "
                f"{values['recovery_calls']} | {values['recovery_tokens']} | {values['candidate_rejections']} |")
        lines.extend(['', f"{condition}: paired n={result['paired']['paired_n']}; "
            f"McNemar p={result['paired']['success_pairs']['exact_two_sided_p']}. "
            'No significance is not equivalence; repeated seeds are layouts within a single task family.', ''])
        failures = [dict(method=m, **failure) for m, v in result['methods'].items() for failure in v['failures']]
        lines.extend(['Failure records (all assigned failures retained):', '```json', json.dumps(failures, indent=2), '```'])
    lines.extend(['', '## Mechanism attribution',
        'Test-only ablation changes only the positive-evidence completion gate. Same observed state '
        'and evidence, no model calls or physical actions. Missing/unknown evidence must not authorize completion. '
        'Ablation accepting these cases is protocol failure, not measured robot failure. See mechanism.json.', '',
        '## Information separation',
        f"Checked {isolation['requests_checked']} recorded model requests. Terminal scoring occurs after execution; "
        'no planner callback in that terminal block. Scoring canary leaves snapshot unchanged. See isolation.json.', '',
        '## Applicability and failures',
        'Restricted known-color two-cube pick/place fixture; controlled 45 mm placement deviation; '
        'hard adds a predeclared goal update. One episode per run, fixed controller and score thresholds. '
        'Not a visual open-world, unseen-object, learned-policy or cross-model result. No uncertain receipt '
        'coverage is inferred from absent events. API/parse failures, rejected candidates, physical failures '
        'and independent terminal failures must remain distinct; raw error/stage are retained above. '
        'Zero failures do not imply zero risk. Costs include rejected noninitial attempts; shared initial '
        'responses are excluded from recovery cost. Physical steps are a proxy, not latency.', '',
        '## Decision',
        'This bundle closes the bounded system evaluation, interface-level mechanism attribution, '
        'bounded information-separation audit and failure/boundary reporting only when the complete-data '
        'gate passes. It does not establish universal performance improvement. If incomplete, the bundle '
        'is interim and no completion claim is authorized.'])
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    return dict(data_complete=results['technical_gate_passed'], isolation_passed=isolation['passed'],
                output=str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(closeout(args.root, args.output)))
