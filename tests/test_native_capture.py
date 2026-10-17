from web_research_sim.core import Store
from web_research_sim.capture import CapturedPage
from web_research_sim.workflow import Workflow


def test_native_capture_needs_no_model_or_search_provider(tmp_path):
    store = Store(tmp_path)
    wid = store.create_world("Native search fixture")["id"]
    def forbidden(*args, **kwargs):
        raise AssertionError("No embedded provider call is permitted")
    def fetch(url):
        return CapturedPage(url, url, b"Fixture", "Fixture", "Fixture", 200, {}, "2026-01-01T00:00:00Z", [])
    workflow = Workflow(store, llm_factory=forbidden, search_factory=forbidden, fetch=fetch)
    result = workflow.capture_discovered(wid, "native-job", sources=[{"url": "https://example.org", "query": "fixture", "rationale": "Official source"}], native_client="fixture-client")
    assert result["captured"] == 1
    assert store.usage(wid)["model_calls"]["reserved"] == 0
    assert store.usage(wid)["search_calls"]["reserved"] == 0
    assert store.usage(wid)["capture_calls"]["reserved"] == 1
    doc = store.captures(wid)[0]
    assert doc["provenance"]["discovery_claim_verified"] is False
    assert doc["provenance"]["ranking_available"] is False
    assert doc["provenance"]["rationale"] == "Official source"


def test_capture_diagnostic_survives_job_and_reservation(tmp_path):
    import json
    from web_research_sim.capture import CaptureError
    store=Store(tmp_path);wid=store.create_world('Failure diagnostics')['id']
    def fail(url):raise CaptureError('dns_failure')
    result=Workflow(store,fetch=fail).capture_discovered(wid,'failed-source',sources=[{'url':'https://example.org'}],native_client='fixture')
    assert result['status']=='partial' and result['failures'][0]['error_code']=='dns_failure'
    with store.db() as db:
        operation=db.execute('SELECT status,result FROM operations').fetchone()
    assert operation['status']=='uncertain'
    assert json.loads(operation['result'])['error_code']=='dns_failure'
    with store.db() as db:
        event=db.execute("SELECT data FROM events WHERE kind='capture_failed'").fetchone()
    assert json.loads(event['data'])['error_code']=='dns_failure'
