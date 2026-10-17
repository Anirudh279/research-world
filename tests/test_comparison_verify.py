from copy import deepcopy
from web_research_sim.core import Store
from web_research_sim.sessions import SessionManager
from web_research_sim.workflow import compare
from web_research_sim.bundles import export_snapshot,import_snapshot
from web_research_sim.comparison_verify import verify_comparison
from web_research_sim.report import render_report


def test_shared_comparison_reproduces_from_traces_without_creator_runs(tmp_path):
    source=Store(tmp_path/'source');world=source.create_world('Color?')['id']
    doc=source.add_capture(world,url='https://fixture.example',body=b'Blue.',text='Blue.',captured_at='2026-01-01T00:00:00Z')
    snapshot=source.freeze(world)['id'];sessions=SessionManager(source);ids=[]
    for i in range(2):
        sess=sessions.create(snapshot,provider='fixture',model=str(i),request_id=str(i),tool_limit=2)
        sessions.tool(sess['id'],'read','open',document_id=doc['id'])
        ids.append(sessions.submit(sess['id'],answer='Blue.',citations=[{'document_id':doc['id'],'quote':'Blue.'}])['id'])
    comparison=compare(source,ids)
    assert 'Semantic correctness: unknown' in render_report(comparison)
    target=Store(tmp_path/'recipient');import_snapshot(target,export_snapshot(source,snapshot,redistribution_acknowledged=True))
    with target.db() as db:assert db.execute('SELECT count(*) FROM runs').fetchone()[0]==0
    assert verify_comparison(target,comparison)['matches'] is True
    altered=deepcopy(comparison);altered['metrics'][0]['verified_citations']=99
    assert verify_comparison(target,altered)['matches'] is False
    (target.objects.root/doc['body_sha256']).write_bytes(b'corrupt')
    assert verify_comparison(target,comparison)['matches'] is False
