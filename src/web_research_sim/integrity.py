"""Full manifest-bound byte verification, without source-web access or repair."""
from .core import Replay, SimError, artifact_references, identifier


def verify_snapshot(objects, snapshot_id):
    identifier(snapshot_id, 64)
    result = {'schema': 'snapshot-integrity-v1', 'snapshot_id': snapshot_id,
              'passed': False, 'documents_checked': 0, 'documents_passed': 0,
              'referenced_objects': 0, 'failures': [],
              'scope': 'Manifest, temporal eligibility, original/text/artifact hashes and declared byte sizes. Not extraction fidelity, relevance or publisher authenticity.'}
    try:
        replay = Replay(objects, snapshot_id)
    except (SimError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        result['failures'].append({'scope': 'manifest', 'code': exc.code if isinstance(exc, SimError) else 'invalid_manifest'})
        return result
    references = {snapshot_id}
    for document_id, record in replay.records.items():
        result['documents_checked'] += 1
        try:
            references.update((record['body_sha256'], record['text_sha256']))
            references.update(item['sha256'] for item in artifact_references(record))
            replay.open(document_id)
            result['documents_passed'] += 1
        except (SimError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            result['failures'].append({'scope': 'document', 'document_id': document_id,
                                       'code': exc.code if isinstance(exc, SimError) else 'invalid_record'})
    result['referenced_objects'] = len(references)
    result['passed'] = not result['failures']
    return result
