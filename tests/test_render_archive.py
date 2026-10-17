from types import SimpleNamespace
import hashlib
import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.render_archive import capture_rendered
from web_research_sim.bundles import export_snapshot, import_snapshot


def fixture_renderer(url, *, original_sink, **kwargs):
    at = '2026-01-01T00:00:00Z'
    assets = []
    for address, body, kind in [(url, b'<script src="a.js"></script>', 'document'),
                                (url + 'a.js', b'document.body.textContent="42"', 'script')]:
        metadata = dict(url=address, requested_url=address, status=200, captured_at=at,
                        content_type='text/html', sha256=hashlib.sha256(body).hexdigest(),
                        bytes=len(body), resource_type=kind)
        assets.append(dict(metadata, receipt=original_sink(body, metadata)))
    dom = b'<body>42<a href="/evidence">Evidence</a></body>'
    receipt = original_sink(dom, {'kind': 'derived_rendered_dom'})
    return SimpleNamespace(url=url, requested_url=url, source_sha256=assets[0]['sha256'],
                           rendered_html=dom, text='42', title='Answer', assets=assets,
                           blocked_requests=[], rendered_at=at, browser_version='fixture-only',
                           derived_receipt=receipt, boundary={}, status='completed', isolation='fixture')


def test_rendered_original_and_dom_survive_sharing(tmp_path):
    source = Store(tmp_path/'source'); world = source.create_world('Question')['id']
    record = capture_rendered(source, world, 'https://example.org/', operation_key='one',
                              image_id='fixture', render=fixture_renderer)
    assert source.objects.get(record['body_sha256']) == b'<script src="a.js"></script>'
    assert len(record['artifacts']) == 3
    assert record['links'] == ['https://example.org/evidence']
    snapshot = source.freeze(world)['id']
    target = Store(tmp_path/'target')
    import_snapshot(target, export_snapshot(source, snapshot, redistribution_acknowledged=True))
    assert target.replay(snapshot).open(record['id']) == source.replay(snapshot).open(record['id'])
    with pytest.raises(SimError, match='already reserved'):
        capture_rendered(source, world, 'https://example.org/', operation_key='one',
                         image_id='fixture', render=lambda *a, **k: pytest.fail('duplicate fetch'))


def test_failure_keeps_original_but_admits_no_capture(tmp_path):
    store=Store(tmp_path); world=store.create_world('Question')['id']
    def failure(url, *, original_sink, **kwargs):
        original_sink(b'original', {'url':url, 'status':200, 'captured_at':'2026-01-01T00:00:00Z'})
        raise RuntimeError('browser failed')
    with pytest.raises(RuntimeError):
        capture_rendered(store,world,'https://example.org/',operation_key='one',image_id='fixture',render=failure)
    assert store.captures(world)==[]
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM original_responses').fetchone()[0]==1
        assert db.execute('SELECT status FROM operations').fetchone()[0]=='uncertain'


def test_real_container_archive_roundtrip(tmp_path):
    import os
    image=os.environ.get('RENDER_TEST_IMAGE')
    if not image:
        pytest.skip('Explicit immutable Docker renderer image required')
    def fetch(url, **limits):
        body=(b'<p id="v">pending</p><script src="/a.js"></script>' if url.endswith('/')
              else b'document.querySelector("#v").textContent="Archived 42"')
        return {'url':url,'body':body,'status':200,
                'headers':{'content-type':'text/html' if url.endswith('/') else 'application/javascript'},
                'captured_at':'2026-01-01T00:00:00Z'}
    source=Store(tmp_path/'source');world=source.create_world('Rendered fixture')['id']
    record=capture_rendered(source,world,'https://research.test/',operation_key='real',
                            image_id=image,fetch_response=fetch)
    assert source.objects.get(record['text_sha256'])==b'Archived 42'
    assert record['provenance']['boundary']['network']=='none'
    snapshot=source.freeze(world)['id'];target=Store(tmp_path/'target')
    import_snapshot(target,export_snapshot(source,snapshot,redistribution_acknowledged=True))
    assert target.replay(snapshot).open(record['id'])==source.replay(snapshot).open(record['id'])
    assert len(record['artifacts'])==3
