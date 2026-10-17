"""Portable, inert HTML reports. No remote resources or executable source text."""
from html import escape
import json


def render_report(comparison):
    runs = comparison["runs"]
    snapshot = comparison["snapshot_id"]
    if not runs or any(run["snapshot_id"] != snapshot for run in runs):
        raise ValueError("Every report run must use the same snapshot")
    e = lambda value: escape(str(value), quote=True)
    sections = []
    for run in runs:
        checks = comparison.get("evidence_checks", {}).get(run["id"])
        evidence_html = '<p>Evidence checks unavailable in this report.</p>'
        if checks is not None:
            metrics = checks["metrics"]
            evidence_html = f'''<h3>Evidence checks</h3><p>{e(metrics['valid_citations'])} of {e(metrics['citation_count'])} citations pass source membership, time, integrity, exact-quote and recorded-access checks.</p>
<p>Observed documents: {e(metrics['observed_documents'])}. Semantic correctness: unknown. No hidden reference panel was used.</p>
<details><summary>Inspect reproducible evidence checks</summary><pre>{e(json.dumps(checks, indent=2, ensure_ascii=False))}</pre></details>'''
        citations = "".join(
            f'<li><code>{e(c.get("document_id", "invalid"))}</code><blockquote>{e(c.get("quote", ""))}</blockquote></li>'
            for c in run.get("citations", []) if isinstance(c, dict))
        cost = run.get("cost_usd")
        cost_label = "Unknown (not zero)" if cost is None else "$" + e(cost)
        sections.append(f'''<article><h2>{e(run.get("provider", "unknown"))} / {e(run.get("model", "unknown"))}</h2>
<dl><dt>Run</dt><dd>{e(run["id"])}</dd><dt>Status</dt><dd>{e(run.get("status"))}</dd>
<dt>Observed model identities</dt><dd>{e(", ".join(run.get("observed_models", [])) or "Unknown")}</dd>
<dt>Recorded trace steps</dt><dd>{len(run.get("trace", []))}</dd><dt>Cost</dt><dd>{cost_label}</dd>
<dt>Invalid citations</dt><dd>{len(run.get("citation_errors", []))}</dd></dl>
<h3>Answer</h3><div class="answer">{e(run.get("answer", ""))}</div>
<h3>Citations</h3><ol>{citations}</ol>
{evidence_html}
<p>{e(run.get("evaluation", "Evaluation unavailable"))}</p>
<p>Isolation: {e(run.get("isolation", "Unverified"))}</p>
<details><summary>Inspect recorded tool trace</summary><pre>{e(json.dumps(run.get("trace", []), indent=2, ensure_ascii=False))}</pre></details></article>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Research World comparison</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1400px;margin:40px auto;padding:0 24px;color:#18252b;background:#fafaf7}}
.runs{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,400px),1fr));gap:24px}}
article{{background:white;border:1px solid #d4ddda;border-radius:12px;padding:24px;min-width:0}}
pre,.answer{{white-space:pre-wrap;overflow-wrap:anywhere}}code,dd{{overflow-wrap:anywhere}}dt{{font-weight:600}}dd{{margin:0 0 10px}}
blockquote{{border-left:3px solid #458575;margin:12px 0;padding-left:12px}}h1,h2{{line-height:1.2}}</style></head>
<body><h1>Same evidence. Different agents.</h1><p>Frozen world: <code>{e(snapshot)}</code></p>
<p>This report records these runs. Fresh model generations can differ. Citation counts and exact quotes alone do not establish answer quality.</p>
<div class="runs">{"".join(sections)}</div>
<footer><h2>Reproduce the evidence checks</h2>
<p>Obtain the corresponding permitted world package and comparison JSON from the creator. With files named world.zip and comparison.json:</p>
<pre>web-research-sim import world.zip --data recipient-data
web-research-sim verify-comparison comparison.json --data recipient-data</pre>
<p>This recomputes deterministic checks, not model generations or trace authenticity. Inspect source redistribution permissions and sensitive content before sharing. This HTML file does not include the world package or comparison JSON.</p></footer></body></html>'''
