"""Reproduce evidence metrics from a portable comparison and imported world."""
from .core import SimError, encoded, digest
from .evaluator import evaluate
from .workflow import comparison_metrics


def verify_comparison(store, comparison):
    if not isinstance(comparison, dict) or comparison.get('schema') != 'research-comparison-v1':
        raise SimError('comparison_schema', 'Expected a research-comparison-v1 artifact.')
    runs = comparison.get('runs')
    if not isinstance(runs, list) or not 2 <= len(runs) <= 10:
        raise SimError('invalid_comparison', 'Expected 2–10 recorded runs.')
    try:
        if len({r['id'] for r in runs}) != len(runs) or any(r['snapshot_id'] != comparison['snapshot_id'] for r in runs):
            raise SimError('snapshot_mismatch', 'Runs must be distinct and use the shared snapshot.')
        replay = store.replay(comparison['snapshot_id'])
        evaluations = {r['id']: evaluate(replay, r) for r in runs}
        metrics = comparison_metrics(runs, evaluations)
    except (KeyError, TypeError, ValueError):
        raise SimError('invalid_comparison', 'The comparison contains malformed run data.') from None
    matches = (encoded(metrics) == encoded(comparison.get('metrics')) and
               encoded(evaluations) == encoded(comparison.get('evidence_checks')))
    return {'schema': 'comparison-verification-v1', 'comparison_sha256': digest(encoded(comparison)),
            'snapshot_id': comparison['snapshot_id'], 'matches': matches,
            'metrics': metrics, 'evidence_checks': evaluations,
            'limitations': ['Verifies reproducibility against this imported world, not authenticity of supplied traces or model identity.',
                            'Semantic correctness, actual billing and outside knowledge are not established.',
                            'No model or live-web request is made.']}
