import pytest
from web_research_sim.report import render_report


def test_report_preserves_results_without_executing_source_content():
    run = {"id": "run", "snapshot_id": "snapshot", "model": "fixture", "provider": "fixture",
           "answer": '<script>alert("secret")</script>', "cost_usd": None,
           "citations": [{"document_id": "doc", "quote": '<img src=x onerror="bad()">'}],
           "trace": [{"observation": {"text": "</pre><script>bad()</script>"}}]}
    html = render_report({"snapshot_id": "snapshot", "runs": [run]})
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html
    assert "Unknown (not zero)" in html
    assert "default-src 'none'" in html
    assert "world package" in html


def test_mixed_snapshots_are_rejected():
    with pytest.raises(ValueError):
        render_report({"snapshot_id": "a", "runs": [{"snapshot_id": "b"}]})
