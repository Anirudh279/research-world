"""Immutable originals, point-in-time views and durable pre-call reservations.

Replay reads only manifest-bound archive objects (local or configured S3).
It never fetches source pages.
SQLite serializes admission before a caller can transmit an external operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


class SimError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        return dt.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError) as exc:
        raise SimError("invalid_time", "Use an ISO timestamp with an explicit timezone.") from exc


def identifier(value, length=32):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{" + str(length) + "}", value):
        raise SimError("invalid_id", "Invalid resource identifier.")
    return value


def artifact_references(record):
    artifacts = record.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) > 128:
        raise SimError("invalid_artifacts", "Use at most 128 source/derived artifact references.")
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"sha256", "bytes", "kind", "url", "captured_at"}:
            raise SimError("invalid_artifacts", "Artifact references require digest, byte size, kind, URL and capture time.")
        identifier(item["sha256"],64)
        if type(item["bytes"]) is not int or not 0 <= item["bytes"] <= 16*1024**2 or item["kind"] not in {"source_asset", "derived_dom"} or not isinstance(item["url"],str) or not 1 <= len(item["url"]) <= 8192:
            raise SimError("invalid_artifacts", "Invalid source/derived artifact metadata.")
        timestamp(item["captured_at"])
    if sum(a["bytes"] for a in artifacts) > 16*1024**2:
        raise SimError("invalid_artifacts", "Referenced artifacts exceed 16 MiB per capture.")
    return artifacts


def eligible_record(record, boundary):
    return (timestamp(record["captured_at"]) <= boundary and timestamp(record["archived_at"]) <= boundary
            and all(timestamp(a["captured_at"]) <= boundary for a in artifact_references(record)))


class Objects:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, raw):
        if not isinstance(raw, bytes) or len(raw) > 16 * 1024**2:
            raise SimError("object_limit", "Object must be bytes and at most 16 MiB.")
        sha = digest(raw)
        path = self.root / sha
        # Publish fully flushed bytes atomically; a racing reader never sees a
        # half-written object under its final content address.
        tmp = self.root / (".tmp-" + uuid.uuid4().hex)
        try:
            with tmp.open("xb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                if self.get(sha) != raw:
                    raise SimError("integrity_error", "Existing object differs from its digest.", 409)
        finally:
            tmp.unlink(missing_ok=True)
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return sha

    def get(self, sha):
        identifier(sha, 64)
        path = self.root / sha
        if path.is_symlink():
            raise SimError("integrity_error", "Object symlinks are not allowed.", 409)
        try:
            with path.open("rb") as f:
                raw = f.read(16 * 1024**2 + 1)
        except FileNotFoundError as exc:
            raise SimError("missing_object", "A referenced archive object is missing.", 409) from exc
        if len(raw) > 16 * 1024**2 or digest(raw) != sha:
            raise SimError("integrity_error", "Archive bytes do not match the content address.", 409)
        return raw


class Store:
    KINDS = {"model_calls", "search_calls", "capture_calls"}

    def __init__(self, root, *, clock=now):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path, self.clock = self.root / "state.sqlite3", clock
        from .schema import initialize_database
        initialize_database(self.db_path)

        from .storage import storage_config, S3Objects
        config = storage_config(self.root)
        binding = encoded(config).decode()
        with self.db(write=True) as db:
            prior = db.execute("SELECT value FROM metadata WHERE key='object_backend'").fetchone()
            if prior and prior[0] != binding:
                raise SimError("storage_config", "Workspace storage selection changed; use a new workspace and import a bundle.")
            if not prior and config['backend'] != 'local' and db.execute("SELECT COUNT(*) FROM worlds").fetchone()[0]:
                raise SimError("storage_config", "Existing local workspace cannot be switched to S3; import into a new workspace.")
            db.execute("INSERT OR IGNORE INTO metadata VALUES('object_backend',?)", (binding,))
        self.objects = Objects(self.root / "objects") if config['backend'] == 'local' else S3Objects(config)

    @contextmanager
    def db(self, *, write=False):
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_world(self, question, budgets=None):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
            raise SimError("invalid_question", "Enter a question of 1–4000 characters.")
        budgets = budgets if budgets is not None else {"model_calls": 12, "search_calls": 5, "capture_calls": 20}
        if set(budgets) != self.KINDS or any(type(v) is not int or not 0 <= v <= 1000 for v in budgets.values()):
            raise SimError("invalid_budget", "Set call limits from zero to 1000 for models, searches and captures.")
        row = {"id": uuid.uuid4().hex, "question": question.strip(), "created_at": self.clock(), "budgets": budgets}
        with self.db(write=True) as db:
            db.execute("INSERT INTO worlds VALUES(?,?,?,?)", (row["id"], row["question"], row["created_at"], encoded(budgets).decode()))
        self.event(row["id"], "world_created", {"question": row["question"], "budgets": budgets})
        return row

    def world(self, world_id):
        identifier(world_id)
        with self.db() as db:
            row = db.execute("SELECT * FROM worlds WHERE id=?", (world_id,)).fetchone()
        if row is None:
            raise SimError("not_found", "World not found.", 404)
        return {**dict(row), "budgets": json.loads(row["budgets"])}

    def worlds(self):
        with self.db() as db:
            rows = db.execute("SELECT id,question,created_at FROM worlds ORDER BY created_at,id").fetchall()
        return [dict(row) for row in rows]

    def event(self, world_id, kind, data):
        self.world(world_id)
        raw = encoded(data)
        if len(raw) > 128 * 1024:
            raise SimError("event_limit", "Event metadata exceeds 128 KiB.")
        with self.db(write=True) as db:
            db.execute("INSERT INTO events(world_id,at,kind,data) VALUES(?,?,?,?)", (world_id, self.clock(), kind, raw.decode()))

    def reserve(self, world_id, kind, operation_key, request):
        world = self.world(world_id)
        if kind not in self.KINDS or not isinstance(operation_key, str) or not 1 <= len(operation_key) <= 200:
            raise SimError("invalid_operation", "Invalid operation kind or idempotency key.")
        request_hash = digest(encoded(request))
        with self.db(write=True) as db:
            limits = json.loads(db.execute("SELECT budgets FROM worlds WHERE id=?", (world_id,)).fetchone()[0])
            existing = db.execute("SELECT * FROM operations WHERE world_id=? AND operation_key=?", (world_id, operation_key)).fetchone()
            if existing:
                if existing["kind"] != kind or existing["request_hash"] != request_hash:
                    raise SimError("operation_conflict", "An operation key cannot be reused with different input.", 409)
                raise SimError("operation_consumed", "This operation is already reserved. Observe its saved outcome; do not resubmit.", 409)
            used = db.execute("SELECT COUNT(*) FROM operations WHERE world_id=? AND kind=?", (world_id, kind)).fetchone()[0]
            if used >= limits[kind]:
                raise SimError("budget_exhausted", f"The {kind} budget is exhausted.", 409)
            op_id = uuid.uuid4().hex
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,NULL)",
                       (op_id, world_id, kind, operation_key, request_hash, self.clock(), "uncertain"))
        return op_id

    def finish(self, operation_id, result, status="completed"):
        identifier(operation_id)
        if status not in {"completed", "failed", "uncertain"}:
            raise SimError("invalid_status", "Unsupported operation state.")
        raw = encoded(result)
        if len(raw) > 128 * 1024:
            raise SimError("operation_limit", "Save large results as objects and reference their digest.")
        with self.db(write=True) as db:
            cursor = db.execute("UPDATE operations SET result=?,status=? WHERE id=? AND result IS NULL", (raw.decode(), status, operation_id))
            if cursor.rowcount != 1:
                raise SimError("operation_consumed", "Operation outcome is missing or already written.", 409)

    def usage(self, world_id):
        world = self.world(world_id)
        with self.db() as db:
            used = dict(db.execute("SELECT kind,COUNT(*) FROM operations WHERE world_id=? GROUP BY kind", (world_id,)).fetchall())
        return {kind: {"limit": world["budgets"][kind], "reserved": used.get(kind, 0)} for kind in sorted(self.KINDS)}

    def set_budgets(self, world_id, budgets):
        self.world(world_id)
        if not isinstance(budgets, dict) or set(budgets) != self.KINDS or any(type(v) is not int or not 0 <= v <= 1000 for v in budgets.values()):
            raise SimError("invalid_budget", "Set all three absolute call limits from 0 to 1000.")
        with self.db(write=True) as db:
            used = dict(db.execute("SELECT kind,COUNT(*) FROM operations WHERE world_id=? GROUP BY kind", (world_id,)).fetchall())
            if any(budgets[k] < used.get(k, 0) for k in self.KINDS):
                raise SimError("budget_below_usage", "Limits cannot be below already reserved calls.", 409)
            db.execute("UPDATE worlds SET budgets=? WHERE id=?", (encoded(budgets).decode(), world_id))
        self.event(world_id, "budgets_set", {"absolute_call_limits": budgets})
        return self.usage(world_id)

    def add_capture(self, world_id, *, url, body, text, captured_at, title="", status=200,
                    headers=None, links=None, requested_url=None, provenance=None,
                    extractor="static-text-v1", extraction_status="completed", extraction_error=None,
                    original_response_id=None, artifacts=None):
        self.world(world_id)
        timestamp(captured_at)
        if not isinstance(text, str) or len(text.encode()) > 4 * 1024**2:
            raise SimError("text_limit", "Extracted text exceeds its limit.")
        raw_hash, text_hash = self.objects.put(body), self.objects.put(text.encode())
        if original_response_id is not None:
            identifier(original_response_id)
            with self.db() as db:
                original = db.execute("SELECT world_id,body_hash FROM original_responses WHERE id=?", (original_response_id,)).fetchone()
            if original is None or original["world_id"] != world_id or original["body_hash"] != raw_hash:
                raise SimError("original_mismatch", "Original receipt does not match this capture.", 409)
        artifact_list = artifact_references({"artifacts": [] if artifacts is None else artifacts})
        for item in artifact_list:
            if len(self.objects.get(item["sha256"])) != item["bytes"]:
                raise SimError("integrity_error", "Artifact byte size mismatch.", 409)
        # Serialize admission before stamping availability: waiting for a writer
        # must not backdate a page into an already elapsed cutoff.
        with self.db(write=True) as db:
            record = {"id": uuid.uuid4().hex, "world_id": world_id, "url": url,
                      "requested_url": requested_url or url, "captured_at": captured_at,
                      "archived_at": self.clock(), "title": str(title)[:1000], "status": int(status),
                      "body_sha256": raw_hash, "body_bytes": len(body), "text_sha256": text_hash,
                      "headers": {k.lower(): str(v) for k, v in (headers or {}).items() if k.lower() in {"content-type", "etag", "last-modified"}},
                      "links": list(links or [])[:200], "provenance": provenance or {}, "extractor": extractor,
                      "extraction_status": extraction_status, "extraction_error": extraction_error,
                      "original_response_id": original_response_id}
            if artifact_list:
                record["artifacts"] = artifact_list
            # Archive availability is set only after original, text and artifacts are durable.
            record_hash = self.objects.put(encoded(record))
            db.execute("INSERT INTO captures VALUES(?,?,?,?,?,?)", (record["id"], world_id, url, captured_at, record["archived_at"], record_hash))
        return record

    def preserve_original(self, world_id, body, metadata):
        """Commit original bytes and a recovery receipt before extraction begins."""
        self.world(world_id)
        raw_hash = self.objects.put(body)
        receipt = uuid.uuid4().hex
        # Only response provenance; no request auth/cookies or arbitrary headers.
        allowed = {key: metadata[key] for key in ("url", "requested_url", "status", "captured_at", "content_type") if key in metadata}
        with self.db(write=True) as db:
            db.execute("INSERT INTO original_responses VALUES(?,?,?,?,?)", (receipt, world_id, raw_hash, encoded(allowed).decode(), self.clock()))
        return receipt

    def captures(self, world_id):
        self.world(world_id)
        with self.db() as db:
            rows = db.execute("SELECT record_hash FROM captures WHERE world_id=? ORDER BY archived_at,id", (world_id,)).fetchall()
        return [json.loads(self.objects.get(row[0])) for row in rows]

    def freeze(self, world_id, cutoff=None):
        world = self.world(world_id)
        cutoff = cutoff or self.clock()
        boundary = timestamp(cutoff)
        if boundary > timestamp(self.clock()):
            raise SimError("future_cutoff", "Cannot freeze a future cutoff.")
        records = [r for r in self.captures(world_id) if eligible_record(r, boundary)]
        manifest = {"schema": "world-v2" if any(r.get("artifacts") for r in records) else "world-v1", "world_id": world_id, "question": world["question"],
                    "cutoff": boundary.isoformat(), "search_version": "lexical-v1", "records": sorted(records, key=lambda r: r["id"])}
        sha = self.objects.put(encoded(manifest))
        with self.db(write=True) as db:
            db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)", (sha, world_id, manifest["cutoff"], len(records)))
        self.event(world_id, "snapshot_frozen", {"id": sha, "count": len(records), "cutoff": manifest["cutoff"]})
        return {"id": sha, "cutoff": manifest["cutoff"], "count": len(records)}

    def replay(self, snapshot_id):
        identifier(snapshot_id, 64)
        with self.db() as db:
            row = db.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not row:
            raise SimError("not_found", "Snapshot not found.", 404)
        replay = Replay(self.objects, snapshot_id)
        if replay.manifest["world_id"] != row["world_id"] or replay.manifest["cutoff"] != row["cutoff"] or len(replay.records) != row["count"]:
            raise SimError("integrity_error", "Snapshot index differs from its manifest.", 409)
        return replay

    def save_run(self, world_id, snapshot_id, data):
        replay = self.replay(snapshot_id)
        if replay.manifest["world_id"] != world_id:
            raise SimError("world_mismatch", "Run and snapshot must belong to the same world.")
        run = {**data, "id": uuid.uuid4().hex, "world_id": world_id, "snapshot_id": snapshot_id, "created_at": self.clock()}
        with self.db(write=True) as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)", (run["id"], world_id, snapshot_id, encoded(run).decode()))
        return run

    def run(self, run_id):
        identifier(run_id)
        with self.db() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise SimError("not_found", "Run not found.", 404)
        return json.loads(row[0])

    def describe(self, world_id):
        world = self.world(world_id)
        with self.db() as db:
            snapshots = [dict(r) for r in db.execute("SELECT * FROM snapshots WHERE world_id=? ORDER BY cutoff,id", (world_id,))]
            runs = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs WHERE world_id=? ORDER BY id", (world_id,))]
            events = [{**dict(r), "data": json.loads(r["data"])} for r in db.execute("SELECT * FROM events WHERE world_id=? ORDER BY id DESC LIMIT 500", (world_id,))]
        return {"world": world, "captures": self.captures(world_id), "snapshots": snapshots, "runs": runs,
                "events": list(reversed(events)), "usage": self.usage(world_id)}


class Replay:
    def __init__(self, objects, snapshot_id):
        self.objects, self.snapshot_id = objects, identifier(snapshot_id, 64)
        self.manifest = json.loads(objects.get(snapshot_id))
        m = self.manifest
        if m.get("schema") not in {"world-v1", "world-v2"} or m.get("search_version") != "lexical-v1":
            raise SimError("schema_version", "Unsupported snapshot format or search version.")
        boundary = timestamp(m["cutoff"])
        self.records = {}
        for r in m["records"]:
            if m["schema"] == "world-v1" and r.get("artifacts"):
                raise SimError("schema_version", "Artifact-bearing records require world-v2.")
            identifier(r["id"])
            if r["id"] in self.records or r["world_id"] != m["world_id"] or not eligible_record(r, boundary):
                raise SimError("temporal_integrity", "Snapshot contains an ineligible or duplicate record.", 409)
            self.records[r["id"]] = r

    def open(self, document_id):
        if not isinstance(document_id, str):
            raise SimError("invalid_id", "Document identifier must be a string.")
        record = self.records.get(document_id)
        if record is None:
            raise SimError("archive_miss", "Document is not in this frozen world.", 404)
        for artifact in artifact_references(record):
            if len(self.objects.get(artifact["sha256"])) != artifact["bytes"]:
                raise SimError("integrity_error", "Artifact byte size mismatch.", 409)
        body = self.objects.get(record["body_sha256"])
        if len(body) != record["body_bytes"]:
            raise SimError("integrity_error", "Original response size mismatch.", 409)
        return {**record, "snapshot_id": self.snapshot_id, "text": self.objects.get(record["text_sha256"]).decode("utf-8")}

    def read(self, document_id, offset=0, max_chars=16000):
        """Read a bounded Unicode-character window from verified archived text."""
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 20000:
            raise SimError("invalid_page", "Use a nonnegative integer offset and max_chars from 1 to 20000.")
        doc = self.open(document_id)
        text = doc["text"]
        if offset > len(text):
            raise SimError("invalid_page", "Offset exceeds the archived text length.")
        end = min(len(text), offset + max_chars)
        return {**doc, "text": text[offset:end], "offset": offset,
                "total_chars": len(text), "next_offset": end if end < len(text) else None,
                "text_truncated": offset > 0 or end < len(text), "read_version": "character-window-v1"}

    def search(self, query, limit=10):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000 or type(limit) is not int or not 1 <= limit <= 50:
            raise SimError("invalid_search", "Use a nonempty query and a limit from 1 to 50.")
        terms = sorted(set(re.findall(r"\w+", query.casefold())))
        latest = {}
        for r in self.records.values():
            old = latest.get(r["url"])
            key = lambda v: (timestamp(v["captured_at"]), timestamp(v["archived_at"]), v["id"])
            if old is None or key(r) > key(old):
                latest[r["url"]] = r
        hits = []
        for r in latest.values():
            doc = self.open(r["id"])
            words = re.findall(r"\w+", (doc["title"] + " " + doc["text"]).casefold())
            score = sum(words.count(t) for t in terms)
            if score:
                hits.append({"id": r["id"], "url": r["url"], "title": r["title"], "score": score,
                             "snippet": doc["text"][:400], "captured_at": r["captured_at"], "body_sha256": r["body_sha256"]})
        return sorted(hits, key=lambda h: (-h["score"], h["url"], h["id"]))[:limit]

    def find(self, document_id, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 1000:
            raise SimError("invalid_query", "Find requires 1–1000 characters.")
        text = self.open(document_id)["text"]
        return [{"start": match.start(), "end": match.end(), "context": text[max(0, match.start()-100):match.end()+100]}
                for match in islice(re.finditer(re.escape(query), text, re.IGNORECASE), 100)]

    def follow(self, document_id, url):
        source = self.open(document_id)
        if not isinstance(url, str) or url not in source["links"]:
            raise SimError("link_not_in_source", "Select an exact link recorded in this document.", 404)
        candidates = [r for r in self.records.values() if r["url"] == url or r["requested_url"] == url]
        if not candidates:
            raise SimError("archive_miss", "This link was not captured in the frozen world. No live request was made.", 404)
        latest = max(candidates, key=lambda r: (timestamp(r["captured_at"]), timestamp(r["archived_at"]), r["id"]))
        return self.open(latest["id"])
