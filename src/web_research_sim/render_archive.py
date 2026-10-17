"""Explicit rendered capture with durable originals and portable artifact lineage.

This host-side entry point requires Docker access. It is not exposed to arbitrary
HTTP callers and does not mount the Docker socket in the product container.
"""
import hashlib
from urllib.parse import urldefrag

from .core import SimError
from .isolated_renderer import render_isolated
from .raw_capture import fetch_raw_response
from .capture import _extract


def capture_rendered(store, world_id, url, *, operation_key, image_id,
                     render=render_isolated, fetch_response=fetch_raw_response):
    """One explicitly selected page; repeated operation keys never fetch again.

    One capture slot covers a bounded browser operation (at most 30 requests,
    10 MiB of source bytes). Source requests and bytes remain separately visible.
    """
    operation = store.reserve(world_id, "capture_calls", operation_key,
                              {"url": url, "image_id": image_id, "mode": "isolated-render-v1"})

    def persist(body, metadata):
        if metadata.get("kind") == "derived_rendered_dom":
            return {"sha256": store.objects.put(body), "kind": "derived_dom"}
        return store.preserve_original(world_id, body, metadata)

    try:
        result = render(url, image_id=image_id, fetch_response=fetch_response,
                        original_sink=persist, enabled=True)
        final_url = urldefrag(result.url)[0]
        documents = [a for a in result.assets if a["url"] == final_url
                     and a.get("resource_type") == "document"
                     and a["sha256"] == result.source_sha256
                     and not 300 <= a["status"] < 400]
        if not documents:
            raise SimError("original_mismatch", "Rendered source original is missing.", 409)
        source = documents[-1]
        dom_hash = hashlib.sha256(result.rendered_html).hexdigest()
        if result.derived_receipt != {"sha256": dom_hash, "kind": "derived_dom"}:
            raise SimError("integrity_error", "Rendered DOM persistence receipt differs.", 409)
        artifacts = [{"sha256": a["sha256"], "bytes": a["bytes"],
                      "kind": "source_asset", "url": a["url"], "captured_at": a["captured_at"]}
                     for a in result.assets]
        artifacts.append({"sha256": dom_hash, "bytes": len(result.rendered_html),
                          "kind": "derived_dom", "url": result.url, "captured_at": result.rendered_at})
        _, _, links = _extract(result.rendered_html, "text/html; charset=utf-8", result.url)
        record = store.add_capture(
            world_id, url=result.url, requested_url=result.requested_url,
            body=store.objects.get(source["sha256"]), text=result.text, title=result.title,
            captured_at=source["captured_at"], status=source["status"],
            headers={"content-type": source.get("content_type", "")},
            original_response_id=source["receipt"], artifacts=artifacts,
            links=links,
            extractor="isolated-browser-dom-v1", extraction_status=result.status,
            extraction_error="blocked_resources" if result.blocked_requests else None,
            provenance={"operation_id": operation, "browser_version": result.browser_version,
                        "boundary": result.boundary, "isolation": result.isolation,
                        "blocked_requests": result.blocked_requests,
                        "source_request_count": len(result.assets),
                        "source_bytes": sum(a["bytes"] for a in result.assets),
                        "derived_dom_sha256": dom_hash})
    except Exception as exc:
        store.finish(operation, {"error_type": type(exc).__name__}, "uncertain")
        raise
    store.finish(operation, {"document_id": record["id"], "status": result.status})
    return record
