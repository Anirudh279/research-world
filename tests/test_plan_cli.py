"""CLI transport checks; no live model/provider claim."""
import json
import httpx
import pytest
from web_research_sim import cli
from web_research_sim.client import Client


@pytest.mark.parametrize('handle_status', ['queued', 'completed'])
def test_plan_wait_observes_without_collecting(monkeypatch, capsys, handle_status):
    seen = []
    def handle(request):
        seen.append((request.method, request.url.path))
        if request.method == 'POST':
            assert request.url.path == '/api/worlds/w/plan'
            assert request.headers['idempotency-key'] == 'stable-plan'
            assert 'plan_job_id' not in json.loads(request.content)
            return httpx.Response(202, json={'id':'p','status':handle_status})
        assert request.url.path == '/api/jobs/p'
        return httpx.Response(200, json={'id':'p','status':'completed','result':{'queries':['a query']}})
    monkeypatch.setattr(cli, 'Client', lambda **_: Client('http://test', transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    assert cli.main(['plan','w','--provider','openai','--model','fixture','--search-provider','brave','--request-key','stable-plan','--wait']) == 0
    assert seen == [('POST','/api/worlds/w/plan'),('GET','/api/jobs/p')]
    assert 'a query' in capsys.readouterr().out


def test_collect_passes_explicit_saved_plan(monkeypatch):
    def handle(request):
        assert request.url.path == '/api/worlds/w/collect'
        body = json.loads(request.content)
        assert body['plan_job_id'] == 'approved-plan'
        assert body['max_queries'] == 2 and body['max_pages'] == 4
        assert request.headers['idempotency-key'] == 'stable-approval'
        return httpx.Response(202, json={'id':'c','status':'queued'})
    monkeypatch.setattr(cli, 'Client', lambda **_: Client('http://test', transport=httpx.MockTransport(handle)))
    assert cli.main(['collect','w','--provider','anthropic','--model','fixture','--search-provider','scrappa','--max-queries','2','--max-pages','4','--plan-job','approved-plan','--request-key','stable-approval']) == 0
