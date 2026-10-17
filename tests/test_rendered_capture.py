"""Real browser tests run only with explicitly supplied installed dependencies."""
import os
import hashlib
import pytest
from web_research_sim.capture import CaptureError
from web_research_sim.rendered_capture import render_fixture,render_live

ROOT='https://research.test/'


@pytest.fixture
def browser_paths():
    names={'node_executable':'RENDER_TEST_NODE','playwright_module':'RENDER_TEST_PLAYWRIGHT','browser_executable':'RENDER_TEST_BROWSER'}
    values={key:os.environ.get(env) for key,env in names.items()}
    if not all(values.values()): pytest.skip('Explicit Node/Playwright/browser paths required for actual browser test')
    return values


def test_live_mode_never_silently_browses():
    with pytest.raises(CaptureError,match='live_rendering_disabled'):
        render_live('https://example.org')


def test_opt_in_required():
    with pytest.raises(CaptureError,match='opt_in'):
        render_fixture(ROOT,{},original_sink=lambda *a:None,node_executable='none',playwright_module='none',browser_executable='none')


def test_real_javascript_dynamic_assets_preserved_before_render(browser_paths):
    saved=[]
    resources={
        ROOT:{'body':b'<html><head><title>Fixture</title><link rel="stylesheet" href="/style.css"></head><body><p id="value">pending</p><script src="/app.js"></script></body></html>','headers':{'content-type':'text/html'}},
        ROOT+'style.css':{'body':b'body { color: blue }','headers':{'content-type':'text/css'}},
        ROOT+'app.js':{'body':b'fetch("/data.json").then(r=>r.json()).then(d=>document.querySelector("#value").textContent="Measured "+d.level+" cm")','headers':{'content-type':'application/javascript'}},
        ROOT+'data.json':{'body':b'{"level":47}','headers':{'content-type':'application/json'}}}
    def sink(body,metadata):
        saved.append((body,metadata));return hashlib.sha256(body).hexdigest()
    result=render_fixture(ROOT,resources,enabled=True,original_sink=sink,**browser_paths)
    assert 'Measured 47 cm' in result.text and result.title=='Fixture'
    assert result.source_sha256==hashlib.sha256(resources[ROOT]['body']).hexdigest()
    assert b'Measured 47 cm' not in saved[0][0] and b'Measured 47 cm' in result.rendered_html
    assert {a['url'] for a in result.assets}>=set(resources)
    assert all(m['kind']=='synthetic_original_before_render' for _,m in saved[:-1])
    assert saved[-1][1]['kind']=='derived_rendered_dom'
    assert result.browser_version and 'unverified' in result.isolation


def test_real_browser_blocks_escape_redirect_websocket_and_serviceworker(browser_paths):
    resources={ROOT:{'body':b'''<html><body><p>Guard fixture</p><script>
      fetch('http://127.0.0.1/private').catch(()=>{});
      fetch('/redirect').catch(()=>{});
      new WebSocket('wss://research.test/socket');
      navigator.serviceWorker.register('/worker.js').catch(()=>{});
      fetch('/post',{method:'POST',body:'no'}).catch(()=>{});
      </script></body></html>''','headers':{'content-type':'text/html'}},
      ROOT+'redirect':{'body':b'','status':302,'headers':{'location':'http://169.254.169.254/latest/meta-data/'}}}
    result=render_fixture(ROOT,resources,enabled=True,original_sink=lambda body,meta:hashlib.sha256(body).hexdigest(),**browser_paths)
    reasons={r['reason'] for r in result.blocked_requests}
    assert 'fixture_ip_destination_forbidden' in reasons
    assert 'fixture_redirect_disabled' in reasons
    assert 'websocket_disabled' in reasons and 'non_get_disabled' in reasons
    assert not any(a['url'].endswith('/worker.js') for a in result.assets)
    assert result.status=='partial'


def test_resource_byte_cap_aborts_actual_browser_document(browser_paths):
    resources={ROOT:{'body':b'<html>Too much</html>','headers':{'content-type':'text/html'}}}
    saved=[]
    with pytest.raises(CaptureError):
        render_fixture(ROOT,resources,enabled=True,max_resource_bytes=5,original_sink=lambda *a:saved.append(a),**browser_paths)
    assert not saved


def test_sink_failure_does_not_render_unsaved_original(browser_paths):
    def sink(*args): raise OSError('fixture-storage-failure')
    with pytest.raises(OSError,match='fixture-storage-failure'):
        render_fixture(ROOT,{ROOT:{'body':b'<html>original</html>','headers':{'content-type':'text/html'}}},enabled=True,original_sink=sink,**browser_paths)


def test_actual_browser_request_limit(browser_paths):
    resources={ROOT:{'body':b'<script src="/one.js"></script>','headers':{'content-type':'text/html'}},
               ROOT+'one.js':{'body':b'window.fixture=1','headers':{'content-type':'application/javascript'}}}
    with pytest.raises(CaptureError,match='request_limit'):
        render_fixture(ROOT,resources,enabled=True,max_requests=1,original_sink=lambda body,meta:'fixture',**browser_paths)


def test_actual_browser_infinite_script_hits_wallclock_limit(browser_paths,monkeypatch):
    import time
    from types import SimpleNamespace
    import subprocess
    from web_research_sim import rendered_capture
    token='render-test-timeout-cleanup-fixture'
    monkeypatch.setattr(rendered_capture.uuid,'uuid4',lambda:SimpleNamespace(hex=token))
    started=time.monotonic()
    with pytest.raises(CaptureError,match='render_wallclock_limit'):
        render_fixture(ROOT,{ROOT:{'body':b'<script>while(true){}</script>','headers':{'content-type':'text/html'}}},
                       enabled=True,max_seconds=8,original_sink=lambda body,meta:'fixture',**browser_paths)
    assert time.monotonic()-started<14  # Hard controller deadline plus bounded cleanup.
    processes=subprocess.check_output(['ps','-axo','command'],text=True)
    assert ('--research-render-token='+token) not in processes
