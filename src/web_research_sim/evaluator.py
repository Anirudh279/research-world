"""Deterministic evidence diagnostics; no semantic judge or live requests."""
from copy import deepcopy

from .core import SimError, digest, encoded, timestamp


def evaluate(replay, run, references=None):
    """references are evaluator-only {id,url,quote} items; never added to the world."""
    if run.get('snapshot_id') != replay.snapshot_id:
        raise SimError('evaluation_snapshot_mismatch', 'Run and evaluator must use the same pinned snapshot.', 409)
    references = [] if references is None else references
    if not isinstance(references, list):
        raise SimError('invalid_references', 'Provide evaluator-only reference items as a list.')
    seen = set()
    for ref in references:
        if not isinstance(ref, dict) or not all(isinstance(ref.get(k), str) and ref[k].strip() for k in ('id', 'url', 'quote')) or ref['id'] in seen:
            raise SimError('invalid_references', 'References need unique id, URL and nonempty exact quote.')
        seen.add(ref['id'])
    cutoff = timestamp(replay.manifest['cutoff'])
    documents, errors, temporal = {}, {}, {}
    for did, record in replay.records.items():
        try:
            temporal[did] = timestamp(record['captured_at']) <= cutoff and timestamp(record['archived_at']) <= cutoff
            if not temporal[did]:
                errors[did] = 'temporal_ineligible'
            else:
                documents[did] = replay.open(did)
        except SimError as exc:
            errors[did] = exc.code
    visible, searched = {}, set()
    for step in run.get('trace', []):
        action = step.get('action', {})
        if not isinstance(action, dict):
            continue
        name = action.get('action')
        args = action.get('arguments', action)
        observation = step.get('observation', {})
        if not isinstance(args, dict) or not isinstance(observation, dict):
            continue
        if 'ok' in observation:
            if observation['ok'] is not True or observation.get('snapshot_id') != replay.snapshot_id:
                continue
            observation = observation.get('result', {})
        if not isinstance(observation, dict) or 'error' in observation:
            continue
        if name == 'search':
            searched.update(hit.get('id') for hit in observation.get('results', []) if isinstance(hit, dict) and hit.get('id') in documents)
        elif name in {'open', 'follow'}:
            did = observation.get('id')
            text = observation.get('text')
            if did in documents and isinstance(text, str) and text and text in documents[did]['text']:
                if name == 'open' and args.get('document_id') != did:
                    continue
                visible.setdefault(did, []).append(text)
        elif name == 'find':
            did = args.get('document_id')
            if did in documents:
                for match in observation.get('matches', []):
                    context = match.get('context') if isinstance(match, dict) else None
                    if isinstance(context, str) and context and context in documents[did]['text']:
                        visible.setdefault(did, []).append(context)
    citations = []
    for index, citation in enumerate(run.get('citations', [])):
        citation = citation if isinstance(citation, dict) else {}
        did, quote = citation.get('document_id'), citation.get('quote')
        valid_quote = isinstance(quote, str) and bool(quote.strip())
        member = isinstance(did, str) and did in replay.records
        doc = documents.get(did) if isinstance(did, str) else None
        exact = bool(doc and valid_quote and quote in doc['text'])
        exposed = exact and any(quote in fragment for fragment in visible.get(did, []))
        citations.append({'index': index, 'document_id': did if isinstance(did, str) else None,
                          'member': member, 'temporal_eligible': temporal.get(did) if member else False,
                          'integrity_verified': doc is not None, 'exact_quote': exact, 'observed_quote': exposed,
                          'valid': bool(member and doc and exact and exposed), 'error': errors.get(did) if member else 'not_in_snapshot'})
    items = []
    for ref in references:
        matching = [did for did, r in replay.records.items() if r.get('url') == ref['url'] or r.get('requested_url') == ref['url']]
        if ref.get('document_id') is not None:
            matching = [did for did in matching if did == ref['document_id']]
        available = [did for did in matching if did in documents and ref['quote'] in documents[did]['text']]
        observed = any(ref['quote'] in text for did in available for text in visible.get(did, []))
        cited = any(c['valid'] and c['document_id'] in available and ref['quote'] in run['citations'][c['index']]['quote'] for c in citations)
        diagnosis, evidence = 'unknown', None
        if not matching:
            diagnosis = 'collection_gap'
            evidence = 'Declared reference URL/version absent from the pinned snapshot; not a claim about the live web.'
        elif any(did in errors for did in matching):
            evidence = 'A matching record is ineligible or unreadable; no collection/extraction/retrieval conclusion.'
        elif available:
            if not observed:
                diagnosis, evidence = 'retrieval_miss', 'Exact reference quote is in extracted archive text but absent from recorded tool exposure.'
            elif not cited:
                diagnosis, evidence = 'observed_not_cited', 'Exact reference quote was exposed but not included in a valid citation.'
            else:
                diagnosis, evidence = 'exact_reference_cited', 'Declared quote matches an observed, valid citation; semantic support is ungraded.'
        else:
            for did in matching:
                try:
                    raw = replay.objects.get(documents[did]['body_sha256'])
                except SimError:
                    continue
                if ref['quote'].encode('utf-8') in raw:
                    diagnosis = 'extraction_gap'
                    evidence = 'Exact UTF-8 reference bytes exist in the original response but not extracted text; rendered visibility is unverified.'
                    break
        items.append({'id': ref['id'], 'available_exact': bool(available), 'observed_exact': observed,
                      'cited_exact': cited, 'diagnosis': diagnosis, 'evidence': evidence})
    count = len(items)
    report = {'schema': 'evidence-evaluation-v1', 'snapshot_id': replay.snapshot_id,
              'run_sha256': digest(encoded(run)), 'reference_sha256': digest(encoded(references)),
              'citations': citations, 'reference_items': items,
              'metrics': {'citation_count': len(citations), 'valid_citations': sum(c['valid'] for c in citations),
                          'reference_count': count, 'reference_exact_recall': sum(i['cited_exact'] for i in items) / count if count else None,
                          'observed_reference_coverage': sum(i['observed_exact'] for i in items) / count if count else None,
                          'search_hit_documents': len(searched), 'observed_documents': len(visible)},
              'judge': {'status': 'unknown', 'assessments': []},
              'limitations': ['Exact strings do not establish factual correctness or semantic support.',
                              'Trace exposure is evaluated as supplied; external model identity and whole-agent isolation are unverified.',
                              'Reference recall describes only the declared hidden panel, not internet coverage.']}
    return report


def attach_assessment(report, *, identity, rubric, evidence, uncertainty, verdict):
    """Attach a supplied review, never generate a judgment or overwrite prior ones."""
    if report.get('schema') != 'evidence-evaluation-v1':
        raise SimError('invalid_evaluation', 'Unsupported evaluation report.')
    if not isinstance(identity, dict) or identity.get('kind') not in {'human', 'model'} or not isinstance(identity.get('name'), str) or not identity['name'].strip():
        raise SimError('invalid_assessment', 'Declare human/model reviewer kind and name.')
    if not isinstance(rubric, dict) or not rubric.get('id') or not rubric.get('version'):
        raise SimError('invalid_assessment', 'Declare rubric id and version.')
    if not isinstance(evidence, list) or not evidence or not all(isinstance(e, dict) and e for e in evidence):
        raise SimError('invalid_assessment', 'Attach explicit evidence records for the review.')
    if not isinstance(uncertainty, str) or not uncertainty.strip() or verdict not in {'pass', 'fail', 'inconclusive'}:
        raise SimError('invalid_assessment', 'Supply uncertainty and pass/fail/inconclusive verdict.')
    result = deepcopy(report)
    assessment = {'identity': identity, 'rubric': rubric, 'evidence': evidence, 'uncertainty': uncertainty, 'verdict': verdict,
                  'evaluation_sha256': digest(encoded(report)), 'provenance': 'caller_supplied_not_independently_authenticated'}
    result['judge']['assessments'].append(deepcopy(assessment))
    result['judge']['status'] = 'review_supplied'
    return result
