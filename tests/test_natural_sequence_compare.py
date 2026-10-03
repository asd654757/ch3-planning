from scripts.natural_sequence_compare import summarize


def row(seed, method, success):
    return dict(seed=seed, method=method, success=success, destination_observations=[], model_calls=0)


def test_paired_denominator_and_both_directions():
    rows = [row(0, 'STOP_ON_FAILURE', False), row(0, 'BOUNDED_REOBSERVE', True),
            row(1, 'STOP_ON_FAILURE', True), row(1, 'BOUNDED_REOBSERVE', False)]
    s = summarize(rows, [0, 1])
    assert s['complete'] and s['paired_wins'] == s['paired_losses'] == 1
    assert s['expected_episodes'] == 4


def test_missing_and_duplicate_never_complete():
    rows = [row(0, 'STOP_ON_FAILURE', True)]
    assert not summarize(rows, [0])['complete']
    assert not summarize(rows * 2, [0])['complete']
