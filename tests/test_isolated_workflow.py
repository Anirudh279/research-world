"""Real Docker tool boundary with a scripted model; not a live inference pass."""
import os
import pytest
from web_research_sim.core import Store
from web_research_sim.isolated_replay import IsolatedReplay
from web_research_sim.providers import ModelReply
from web_research_sim.workflow import Workflow
from web_research_sim.evaluator import evaluate


@pytest.mark.skipif(not os.environ.get('WRS_FROZEN_TEST_IMAGE'),reason='Requires reviewed local worker image')
def test_scripted_model_uses_actual_network_disabled_tools(tmp_path):
    store=Store(tmp_path);wid=store.create_world('What color is the archived notice?')['id']
    doc=store.add_capture(wid,url='https://fixture.example',body=b'The notice is blue.',text='The notice is blue.',captured_at='2020-01-01T00:00:00Z')
    snapshot=store.freeze(wid)['id'];adapters=[]
    def factory(objects,sid):
        replay=IsolatedReplay(objects,sid,image_id=os.environ['WRS_FROZEN_TEST_IMAGE']);adapters.append(replay);return replay
    class Model:
        def __init__(self):self.actions=iter([{'action':'search','query':'notice'},{'action':'open','document_id':doc['id']},{'action':'finish','answer':'The notice is blue.','citations':[{'document_id':doc['id'],'quote':'The notice is blue.'}]}])
        def complete(self,messages):
            assert adapters and adapters[0].boundary_receipt # actual ready handshake precedes inference
            return ModelReply(next(self.actions),{},'scripted-not-live',{})
        def close(self):pass
    run=Workflow(store,llm_factory=lambda *a:Model(),cost_admission=None,replay_factory=factory).research(snapshot,'actual-isolated-fixture',model_provider='fixture',model='scripted',max_steps=3)
    assert run['status']=='completed'
    assert len(run['boundary_receipt'])==3
    assert all(r['network']=='none' and r['snapshot_id']==snapshot for r in run['boundary_receipt'])
    assert 'trusted_host_outside_boundary' in run['isolation']
    assert evaluate(store.replay(snapshot),run)['metrics']['valid_citations']==1
    assert not adapters[0].root.exists()
    assert store.usage(wid)['search_calls']['reserved']==0
