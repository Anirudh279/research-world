"""Reproducible synthetic storage/search benchmark; never calls providers."""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import resource
import time
from .core import Store, encoded, digest


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--documents", type=int, default=10000)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.documents <= 10000 or not 5 <= args.samples <= 100:
        parser.error("Use 1–10000 documents and 5–100 samples")
    root = Path(args.data)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "benchmark-started.json").open("x") as out:
        json.dump(vars(args), out)
    started = time.perf_counter()
    store = Store(root)
    world = store.create_world("Synthetic performance corpus; not a research quality benchmark")
    build_started = time.perf_counter()
    documents = []
    total_source_bytes = 0
    for index in range(args.documents):
        topic = ("ecology", "transport", "astronomy", "energy")[index % 4]
        text = f"Synthetic bulletin {index}. Topic {topic}. Region sector{index % 100}. Value {index * 7}. " + ("This fixture measures local retrieval performance, not real research accuracy. " * 24)
        body = ("<html><p>" + text + "</p></html>").encode()
        doc = store.add_capture(world["id"], url=f"https://fixture.example/{index}",
            body=body, text=text, title=f"Synthetic {topic} bulletin {index}",
            captured_at="2026-01-01T00:00:00+00:00", provenance={"synthetic": True, "rights": "project-authored fixture"})
        documents.append(doc["id"])
        total_source_bytes += len(body)
        if (index + 1) % 1000 == 0:
            print(json.dumps({"phase": "build", "documents": index + 1}), flush=True)
    build_seconds = time.perf_counter() - build_started
    freeze_started = time.perf_counter()
    snapshot = store.freeze(world["id"])
    freeze_seconds = time.perf_counter() - freeze_started
    searches, opens, hashes = [], [], []
    # Each request reloads and validates the manifest, matching current HTTP
    # entrypoint behavior. OS caches are not flushed; first and later timings kept.
    for index in range(args.samples):
        query = f"ecology sector{index % 100}"
        begin = time.perf_counter()
        result = store.replay(snapshot["id"]).search(query, 10)
        searches.append((time.perf_counter() - begin) * 1000)
        hashes.append(digest(encoded(result)))
        begin = time.perf_counter()
        store.replay(snapshot["id"]).open(documents[index % len(documents)])
        opens.append((time.perf_counter() - begin) * 1000)
        if (index + 1) % 5 == 0:
            print(json.dumps({"phase": "query", "samples": index + 1}), flush=True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    receipt = {"schema": "performance-v1", "synthetic": True, "documents": args.documents,
        "source_bytes": total_source_bytes, "samples": args.samples,
        "query_pattern": "ecology sector{sample_index modulo 100}",
        "search_version": "lexical-v1", "snapshot_id": snapshot["id"],
        "build_seconds": build_seconds, "freeze_seconds": freeze_seconds,
        "total_seconds": time.perf_counter() - started,
        "search_ms": {"samples": searches, "p50": percentile(searches, .5), "p95": percentile(searches, .95)},
        "open_ms": {"samples": opens, "p50": percentile(opens, .5), "p95": percentile(opens, .95)},
        "normalized_search_hashes": hashes,
        "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
        "workspace_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
        "visible_cpu_count": os.cpu_count(), "cgroup_cpu_max": None, "cgroup_memory_max": None,
        "limitations": ["Synthetic text corpus, no PDF/browser/media fidelity measurement", "OS caches not flushed", "Includes manifest reload/hash checking, excludes HTTP/client transport", "No provider/model calls", "Peak RSS is this Python process, not entire host"]}
    for key, path in (("cgroup_cpu_max", "/sys/fs/cgroup/cpu.max"), ("cgroup_memory_max", "/sys/fs/cgroup/memory.max")):
        if Path(path).exists():
            receipt[key] = Path(path).read_text().strip()
    with Path(args.output).open("x") as out:
        json.dump(receipt, out, indent=2)
    print(json.dumps({"complete": True, "search_p95_ms": receipt["search_ms"]["p95"], "open_p95_ms": receipt["open_ms"]["p95"], "peak_rss_bytes": receipt["peak_rss_bytes"]}), flush=True)


if __name__ == "__main__":
    main()
