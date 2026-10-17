import json

import pytest

from web_research_sim.core import SimError, Store, encoded
from web_research_sim.evaluator import attach_assessment, evaluate


@pytest.mark.parametrize('label,expected', [
    ('exact_observed', 'exact_reference_cited'),
    ('unsupported_quote', 'observed_not_cited'),
    ('foreign_document', 'observed_not_cited'),
    ('no_tools', 'retrieval_miss'),
    ('search_only', 'retrieval_miss'),
    ('truncated_open', 'retrieval_miss'),
    ('find_exposure', 'exact_reference_cited'),
    ('native_trace', 'exact_reference_cited'),
    ('missing_source', 'collection_gap'),
    ('raw_only', 'extraction_gap'),
    ('unexplained_missing_quote', 'unknown'),
    ('corrupt_original', 'unknown'),
])
def test_twelve_labeled_deterministic_cases_use_hidden_references(tmp_path, label, expected):
    world_dir = tmp_path / 'agent-world'
    evaluator_dir = tmp_path / 'evaluator-only'
    evaluator_dir.mkdir()
    store = Store(world_dir, clock=lambda: '2026-01-02T00:00:00Z')
    world = store.create_world('What is the color?')['id']
    quote = 'The color is blue.'
    body = quote.encode() if label != 'unexplained_missing_quote' else b'Other original'
    text = quote if label not in {'raw_only', 'unexplained_missing_quote'} else 'Other extracted text'
    doc = store.add_capture(world, url='https://example.org/color', body=body, text=text, captured_at='2026-01-01T00:00:00Z')
    snapshot = store.freeze(world)['id']
    replay = store.replay(snapshot)
    refs = [{'id': 'hidden-panel-item-1', 'url': doc['url'], 'quote': quote}]
    if label == 'missing_source':
        refs[0]['url'] = 'https://example.org/absent'
    refs_file = evaluator_dir / 'references.json'
    refs_file.write_bytes(encoded(refs))
    opened = {'action': {'action': 'open', 'document_id': doc['id']}, 'observation': {**doc, 'text': text}}
    trace = [opened]
    citations = [{'document_id': doc['id'], 'quote': quote}]
    if label == 'unsupported_quote':
        citations[0]['quote'] = 'The color is red.'
    elif label == 'foreign_document':
        citations[0]['document_id'] = 'f' * 32
    elif label == 'no_tools':
        trace = []
    elif label == 'search_only':
        trace = [{'action': {'action': 'search', 'query': 'blue'}, 'observation': {'results': [{'id': doc['id'], 'snippet': quote}]}}]
    elif label == 'truncated_open':
        trace[0]['observation']['text'] = 'The color'
    elif label == 'find_exposure':
        trace = [{'action': {'action': 'find', 'document_id': doc['id'], 'query': 'blue'}, 'observation': {'matches': [{'context': quote}]}}]
    elif label == 'native_trace':
        trace = [{'action': {'action': 'open', 'arguments': {'document_id': doc['id']}},
                  'observation': {'ok': True, 'snapshot_id': snapshot, 'result': {**doc, 'text': text}}}]
    elif label == 'corrupt_original':
        (store.objects.root / doc['body_sha256']).write_bytes(b'corrupted')
    run = {'snapshot_id': snapshot, 'answer': 'Blue.', 'citations': citations, 'trace': trace}
    before = sorted(p.name for p in store.objects.root.iterdir())
    report = evaluate(replay, run, json.loads(refs_file.read_bytes()))
    assert report['reference_items'][0]['diagnosis'] == expected
    assert report['metrics']['reference_exact_recall'] == (1.0 if expected == 'exact_reference_cited' else 0.0)
    assert report['judge'] == {'status': 'unknown', 'assessments': []}
    assert sorted(p.name for p in store.objects.root.iterdir()) == before
    assert not any(b'hidden-panel-item-1' in p.read_bytes() for p in store.objects.root.iterdir())


def test_temporal_ineligibility_is_not_semantic_or_collection_failure(tmp_path):
    store = Store(tmp_path, clock=lambda: '2026-01-02T00:00:00Z')
    world = store.create_world('Time test')['id']
    doc = store.add_capture(world, url='https://example.org', body=b'known', text='known', captured_at='2026-01-01T00:00:00Z')
    replay = store.replay(store.freeze(world)['id'])
    # Defense in depth when an evaluator receives a malformed replay implementation.
    replay.records[doc['id']]['archived_at'] = '2026-01-03T00:00:00Z'
    run = {'snapshot_id': replay.snapshot_id, 'citations': [{'document_id': doc['id'], 'quote': 'known'}], 'trace': []}
    report = evaluate(replay, run, [{'id': 'time', 'url': doc['url'], 'quote': 'known'}])
    assert report['citations'][0]['temporal_eligible'] is False
    assert report['citations'][0]['valid'] is False
    assert report['reference_items'][0]['diagnosis'] == 'unknown'


def test_supplied_judge_assessment_preserves_unknown_original_and_provenance(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Review test')['id']
    replay = store.replay(store.freeze(world)['id'])
    report = evaluate(replay, {'snapshot_id': replay.snapshot_id, 'citations': [], 'trace': []})
    assert report['metrics']['reference_exact_recall'] is None
    reviewed = attach_assessment(report, identity={'kind': 'human', 'name': 'Reviewer example'},
        rubric={'id': 'support', 'version': '1'}, evidence=[{'quote': 'Example review evidence', 'location': 'review-notes:1'}],
        uncertainty='Authored test assessment; no actual review performed.', verdict='inconclusive')
    assert report['judge']['status'] == 'unknown'
    assert reviewed['judge']['status'] == 'review_supplied'
    assert reviewed['judge']['assessments'][0]['verdict'] == 'inconclusive'
    assert reviewed['judge']['assessments'][0]['provenance'] == 'caller_supplied_not_independently_authenticated'
    with pytest.raises(SimError):
        attach_assessment(report, identity={'kind': 'model', 'name': 'unverified'}, rubric={'id': 'r', 'version': '1'}, evidence=[], uncertainty='unknown', verdict='pass')


def test_all_twelve_hidden_starter_tasks_keep_semantic_verdict_unknown(tmp_path):
    from pathlib import Path
    from web_research_sim.core import digest
    from web_research_sim.starters import install_starter
    evaluation_dir = Path(__file__).resolve().parents[1] / 'evaluation'
    reference_bytes = (evaluation_dir / 'starter-evidence-references-v1.json').read_bytes()
    freeze = json.loads((evaluation_dir / 'starter-evidence-references-v1.freeze.json').read_bytes())
    assert digest(reference_bytes) == freeze['sha256']
    assert len(reference_bytes) == freeze['bytes']
    panel = json.loads(reference_bytes)
    candidates = json.loads((evaluation_dir / 'starter-reference-tasks-v1.json').read_bytes())
    tasks = {task['id']: task for task in candidates['tasks']}
    assert len(tasks) == len(panel['tasks']) == 12
    store = Store(tmp_path / 'agent-world')
    worlds = {slug: install_starter(store, slug) for slug in {'river', 'energy', 'civic'}}
    for case in panel['tasks']:
        task = tasks[case['task_id']]
        installation = worlds[case['starter']]
        snapshot = next(s['id'] for s in installation['snapshots'] if s['label'] == case['snapshot'])
        run = {'snapshot_id': snapshot, 'answer': task['answer'], 'citations': [], 'trace': []}
        report = evaluate(store.replay(snapshot), run, case['references'])
        assert all(item['available_exact'] for item in report['reference_items'])
        assert report['metrics']['reference_exact_recall'] == 0
        assert report['judge']['status'] == 'unknown'
        assert not any(item['cited_exact'] for item in report['reference_items'])
        assert 'label' not in report  # Authored semantic labels are not evaluator verdicts.
