import pytest
from web_research_sim import capture
from web_research_sim.core import Store, SimError
from test_capture import _response_fixture


def test_original_is_durable_before_extractor_crash(tmp_path, monkeypatch):
    store = Store(tmp_path)
    wid = store.create_world("Crash fixture")["id"]
    body = b"Original response survives parser crash"
    _response_fixture(monkeypatch, body, headers={"content-type": "text/plain"})
    def extract(*args):
        with Store(tmp_path).db() as db:
            row = db.execute("SELECT * FROM original_responses").fetchone()
        assert row["world_id"] == wid
        assert store.objects.get(row["body_hash"]) == body
        raise RuntimeError("Injected extractor crash")
    monkeypatch.setattr(capture, "_extract", extract)
    with pytest.raises(RuntimeError, match="Injected"):
        capture.fetch_page("https://example.org", original_sink=lambda raw, meta: store.preserve_original(wid, raw, meta))
    assert store.captures(wid) == []
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM original_responses").fetchone()[0] == 1


def test_original_receipt_cannot_be_rebound(tmp_path):
    store = Store(tmp_path)
    wid = store.create_world("Receipt fixture")["id"]
    receipt = store.preserve_original(wid, b"Original", {})
    with pytest.raises(SimError) as exc:
        store.add_capture(wid, url="https://example.org", body=b"Different", text="Different", captured_at="2026-01-01T00:00:00Z", original_response_id=receipt)
    assert exc.value.code == "original_mismatch"
