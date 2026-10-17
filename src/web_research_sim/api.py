"""Single-process local API and dashboard; jobs persist and are never auto-replayed."""
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
import asyncio
import threading
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .core import Store, SimError, encoded, identifier
from .workflow import Workflow, compare
from .sessions import SessionManager
from .scheduler import ScheduleManager


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewWorld(Body):
    question: str = Field(min_length=1, max_length=4000)
    budgets: dict[str, int] | None = None


class CollectionPlan(Body):
    model_provider: str
    model: str = Field(min_length=1, max_length=200)
    search_provider: str
    max_queries: int = Field(default=3, ge=1, le=10)
    max_pages: int = Field(default=5, ge=1, le=50)


class Collection(CollectionPlan):
    plan_job_id: str | None = None

class Research(Body):
    model_provider: str
    model: str = Field(min_length=1, max_length=200)
    max_steps: int = Field(default=8, ge=1, le=30)


class Freeze(Body):
    cutoff: str | None = None


class Comparison(Body):
    run_ids: list[str]


class Budgets(Body):
    model_calls: int = Field(ge=0, le=1000)
    search_calls: int = Field(ge=0, le=1000)
    capture_calls: int = Field(ge=0, le=1000)


from .source_schema import DiscoveredSource


class NativeCapture(Body):
    sources: list[DiscoveredSource] = Field(min_length=1, max_length=20)
    native_client: str = Field(min_length=1, max_length=100)


class SessionStart(Body):
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    tool_limit: int = Field(default=30, ge=1, le=1000)
    request_id: str = Field(min_length=1, max_length=200)


class SessionTool(Body):
    operation_key: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=30)
    arguments: dict = Field(default_factory=dict)


class SessionSubmit(Body):
    answer: str = Field(min_length=1, max_length=100000)
    citations: list[dict] = Field(default_factory=list, max_length=100)


class SessionReset(Body):
    request_id: str = Field(min_length=1, max_length=200)


class Empty(Body):
    pass


class ScheduleCreate(Body):
    kind: str
    config: dict
    interval_seconds: int = Field(ge=1)
    max_runs: int = Field(ge=1, le=1000)
    start_at: str
    request_id: str = Field(min_length=1, max_length=200)


def create_app(data_dir=None, *, workflow_factory=Workflow, scheduler_poll_seconds=1.0):
    store = Store(data_dir or os.environ.get("WR_SIM_DATA", "data"))
    with store.db(write=True) as db:
        # Request keys are workspace-wide and never expire automatically. Binding
        # and job insertion share one transaction so response loss cannot create
        # a second job when the caller repeats the same explicit request key.
        db.execute("""CREATE TABLE IF NOT EXISTS job_request_keys(
            request_key TEXT PRIMARY KEY, request_json TEXT NOT NULL,
            job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id))""")
    sessions = SessionManager(store)
    workflow = workflow_factory(store)
    scheduler = ScheduleManager(store, workflow=workflow, owner_id="api-scheduler")
    stop_scheduler = threading.Event()
    scheduler_state = {"status": "not_started", "error_type": None}
    def scheduler_loop():
        scheduler_state["status"] = "running"
        while not stop_scheduler.is_set():
            try:
                scheduler.tick()
            except Exception as exc:
                # Preserve uncertain slots; do not automatically retry a failed
                # controller tick. A verified restart can inspect/recover them.
                scheduler_state.update(status="failed", error_type=type(exc).__name__)
                return
            stop_scheduler.wait(scheduler_poll_seconds)
        scheduler_state["status"] = "stopped"
    scheduler_thread = threading.Thread(target=scheduler_loop, name="research-scheduler", daemon=True)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")
    token = os.environ.get("WR_SIM_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        # Prevent a second API process from marking a live owner's jobs stale.
        lock = (store.root / "server.lock").open("a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("Another server owns this workspace. Run one API worker.")
        with store.db(write=True) as db:
            db.execute("UPDATE jobs SET status='interrupted',error=? WHERE status IN ('queued','running')",
                       (encoded({"code": "process_interrupted", "message": "The owner stopped. No automatic resubmission; existing call reservations remain consumed."}).decode(),))
        try:
            scheduler.recover_owned(exclusive_owner_confirmed=True)
            scheduler_state["status"] = "running"
            scheduler_thread.start()
            yield
        finally:
            # Stop polling first. Never release ownership while a scheduled
            # operation could still be writing or consuming provider calls.
            stop_scheduler.set()
            while scheduler_thread.is_alive():
                await asyncio.to_thread(scheduler_thread.join, 0.25)
            await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    app = FastAPI(title="Web Research Sim", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.scheduler_state = scheduler_state

    @app.exception_handler(SimError)
    async def sim_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.url.hostname
        allowed = {"localhost", "127.0.0.1", "::1", "testserver"}
        configured = os.environ.get("WR_SIM_PUBLIC_HOST")
        if configured:
            allowed.add(configured)
            if not token:
                return JSONResponse({"error": {"code": "remote_auth_required", "message": "Set WR_SIM_TOKEN for remote access."}}, status_code=503)
        if host not in allowed:
            return JSONResponse({"error": {"code": "host_denied", "message": "Host is not configured."}}, status_code=403)
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).hostname != host or urlsplit(origin).netloc != request.url.netloc):
            return JSONResponse({"error": {"code": "origin_denied", "message": "Cross-origin requests are disabled."}}, status_code=403)
        if token and request.url.path.startswith("/api/"):
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
                return JSONResponse({"error": {"code": "authentication_required", "message": "Provide the workspace bearer token."}}, status_code=401)
        try:
            length = int(request.headers.get("content-length", "0"))
            if length < 0:
                raise ValueError()
        except ValueError:
            return JSONResponse({"error": {"code": "invalid_length", "message": "Invalid Content-Length."}}, status_code=400)
        if length > 128 * 1024:
            return JSONResponse({"error": {"code": "request_limit", "message": "Request too large."}}, status_code=413)
        if request.method in {"POST", "PUT", "PATCH"}:
            payload = bytearray()
            async for chunk in request.stream():
                payload.extend(chunk)
                if len(payload) > 128 * 1024:
                    return JSONResponse({"error": {"code": "request_limit", "message": "Request too large."}}, status_code=413)
            request._body = bytes(payload)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    def hosted_cost_status():
        from .costs import CostLedger
        return {**CostLedger(store.root).summary(),
                "policy_file_present": (store.root / "cost-policy.json").is_file(),
                "policy_status_meaning": "Presence only. Provider/model match, expiry and request bounds are checked before each hosted call.",
                "scope": "Hosted model/search API admission only; excludes native subscriptions and infrastructure."}

    @app.get("/api/costs")
    def costs_status():
        return hosted_cost_status()

    @app.get("/api/status")
    def status():
        from .storage_diagnostics import storage_status
        return {"version": __version__, "providers": {name: bool(os.environ.get(key)) for name, key in {
            "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "scrappa": "SCRAPPA_API_KEY", "brave": "BRAVE_API_KEY"}.items()},
            "provider_status_meaning": "Credential presence only; actual connection not verified.",
            "hosted_costs": hosted_cost_status(),
            "storage": storage_status(store.root),
            "limitations": ["Development alpha; embedded provider API verification is pending. Native Codex and Claude workflows have completed bounded live tests; capture gaps remain visible.", "Hosted calls require a monetary allocation and reviewed cost bounds. Native client inference and infrastructure costs are outside this cap.", "Scheduled capture and PDF text extraction are implemented; bounded browser capture is implemented, while the 24-hour soak and broader account compatibility remain unverified.", "Frozen tools are archive-only; whole-agent network isolation is not yet certified."]}

    @app.get("/api/starters")
    def starters():
        from .starters import list_starters
        return {"starters": list_starters()}

    @app.post("/api/starters/{name}", status_code=201)
    def install_starter_world(name: str):
        from .starters import install_starter
        return install_starter(store, name)

    @app.get("/api/worlds")
    def worlds():
        return {"worlds": store.worlds()}

    @app.post("/api/worlds", status_code=201)
    def create(body: NewWorld):
        return store.create_world(body.question, body.budgets)

    @app.get("/api/worlds/{world_id}")
    def describe(world_id: str):
        return store.describe(world_id)

    @app.post("/api/worlds/{world_id}/budgets")
    def budgets(world_id: str, body: Budgets):
        return store.set_budgets(world_id, body.model_dump())

    def submit(world_id, kind, fn, *, request_key=None, request_data=None, plan_claim=None):
        store.world(world_id)
        if request_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_key):
            raise SimError("invalid_idempotency_key", "Use 1–128 ASCII letters, digits, dots, underscores, colons or hyphens for Idempotency-Key.")
        binding = encoded({"world_id": world_id, "kind": kind, "request": request_data}).decode()
        job_id = uuid.uuid4().hex
        with store.db(write=True) as db:
            if request_key is not None:
                previous = db.execute("SELECT request_json,job_id FROM job_request_keys WHERE request_key=?", (request_key,)).fetchone()
                if previous:
                    if previous["request_json"] != binding:
                        raise SimError("idempotency_conflict", "This Idempotency-Key is already bound to a different request.", 409)
                    existing = db.execute("SELECT id,status FROM jobs WHERE id=?", (previous["job_id"],)).fetchone()
                    if existing is None:
                        raise SimError("idempotency_integrity", "The request key refers to a missing job; submission is refused.", 409)
                    return {"id": existing["id"], "status": existing["status"], "replayed": True}
            if plan_claim is not None:
                db.execute("CREATE TABLE IF NOT EXISTS collection_plan_uses(plan_job_id TEXT PRIMARY KEY,collection_job_id TEXT NOT NULL UNIQUE)")
                if db.execute("SELECT 1 FROM collection_plan_uses WHERE plan_job_id=?",(plan_claim,)).fetchone():
                    raise SimError("plan_consumed","This plan already has a collection job. Observe that job; do not resubmit.",409)
                db.execute("INSERT INTO collection_plan_uses VALUES(?,?)",(plan_claim,job_id))
            db.execute("INSERT INTO jobs VALUES(?,?,?,'queued',NULL,NULL)", (job_id, world_id, kind))
            if request_key is not None:
                db.execute("INSERT INTO job_request_keys VALUES(?,?,?)", (request_key, binding, job_id))

        def run():
            with store.db(write=True) as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
            try:
                result = fn(job_id)
            except Exception as exc:
                from .providers import public_provider_error
                err = {"code": exc.code, "message": exc.message} if isinstance(exc, SimError) else public_provider_error(exc) or {
                    "code": "workflow_failed", "message": "The operation failed. Check provider configuration and workspace events. Reserved calls remain counted.", "type": type(exc).__name__}
                with store.db(write=True) as db:
                    db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (encoded(err).decode(), job_id))
            else:
                with store.db(write=True) as db:
                    db.execute("UPDATE jobs SET status='completed',result=? WHERE id=?", (encoded(result).decode(), job_id))
        try:
            pool.submit(run)
        except Exception:
            # The durable request stays consumed even when dispatch is uncertain.
            # A repeated key only observes it; it never dispatches another task.
            with store.db(write=True) as db:
                db.execute("UPDATE jobs SET status='interrupted',error=? WHERE id=? AND status='queued'",
                           (encoded({"code": "dispatch_uncertain", "message": "Job dispatch was not confirmed. Inspect this job; this request key will not resubmit it."}).decode(), job_id))
                existing = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            return {"id": job_id, "status": existing["status"]}
        return {"id": job_id, "status": "queued"}

    @app.get("/api/worlds/{world_id}/plans")
    def collection_plans(world_id: str):
        store.world(world_id)
        with store.db() as db:
            rows=db.execute("SELECT id,status FROM jobs WHERE world_id=? AND kind='collection_plan' ORDER BY rowid DESC LIMIT 100",(world_id,)).fetchall()
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collection_plan_uses'").fetchone()
            claims=dict(db.execute("SELECT plan_job_id,collection_job_id FROM collection_plan_uses WHERE plan_job_id IN (SELECT id FROM jobs WHERE world_id=? AND kind='collection_plan' ORDER BY rowid DESC LIMIT 100)",(world_id,)).fetchall()) if exists else {}
        return {"plans":[{**dict(row),"collection_job_id":claims.get(row['id'])} for row in rows]}

    @app.post("/api/worlds/{world_id}/plan", status_code=202)
    def plan_collection(world_id: str, body: CollectionPlan, request: Request):
        return submit(world_id,"collection_plan",lambda job_id: workflow.plan_collection(world_id,job_id,**body.model_dump()),
                      request_key=request.headers.get("idempotency-key"),request_data={"world_id":world_id,**body.model_dump()})

    @app.post("/api/worlds/{world_id}/collect", status_code=202)
    def collect(world_id: str, body: Collection, request: Request):
        config=body.model_dump(exclude={"plan_job_id"});plan=None
        if body.plan_job_id is not None:
            identifier(body.plan_job_id)
            with store.db() as db:
                row=db.execute("SELECT * FROM jobs WHERE id=?",(body.plan_job_id,)).fetchone()
            if not row or row['world_id']!=world_id or row['kind']!='collection_plan' or row['status']!='completed':
                raise SimError('plan_not_ready','Select a completed collection plan from this world.',409)
            try:plan=json.loads(row['result'])
            except (ValueError,TypeError):raise SimError('invalid_plan_binding','Saved plan result is invalid.',409) from None
            if not isinstance(plan,dict) or plan.get('plan_job_id')!=body.plan_job_id:
                raise SimError('invalid_plan_binding','Saved plan identity does not match.',409)
            workflow.validate_prepared_plan(world_id,plan,config)
        kwargs={**config,**({'prepared_plan':plan} if plan is not None else {})}
        return submit(world_id,"collection",lambda job_id:workflow.collect(world_id,job_id,**kwargs),
                      request_key=request.headers.get("idempotency-key"),request_data={"world_id":world_id,**body.model_dump()},plan_claim=body.plan_job_id)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        identifier(job_id)
        with store.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise SimError("not_found", "Job not found.", 404)
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None, "error": json.loads(row["error"]) if row["error"] else None}

    @app.post("/api/worlds/{world_id}/capture", status_code=202)
    def capture_native(world_id: str, body: NativeCapture, request: Request):
        return submit(world_id, "native_capture", lambda job_id: workflow.capture_discovered(world_id, job_id, **body.model_dump()),
                      request_key=request.headers.get("idempotency-key"), request_data={"world_id": world_id, **body.model_dump()})

    @app.post("/api/worlds/{world_id}/freeze")
    def freeze(world_id: str, body: Freeze):
        return store.freeze(world_id, body.cutoff)

    @app.post("/api/snapshots/{snapshot_id}/research", status_code=202)
    def research(snapshot_id: str, body: Research, request: Request):
        world_id = store.replay(snapshot_id).manifest["world_id"]
        return submit(world_id, "research", lambda job_id: workflow.research(snapshot_id, job_id, **body.model_dump()),
                      request_key=request.headers.get("idempotency-key"), request_data={"snapshot_id": snapshot_id, **body.model_dump()})

    @app.get("/api/snapshots/{snapshot_id}/search")
    def search(snapshot_id: str, q: str, limit: int = 10):
        return {"results": store.replay(snapshot_id).search(q, limit)}

    @app.get("/api/snapshots/{snapshot_id}/documents/{document_id}")
    def document(snapshot_id: str, document_id: str):
        return store.replay(snapshot_id).open(document_id)

    @app.get("/api/snapshots/{snapshot_id}/documents/{document_id}/read")
    def read_document(snapshot_id: str, document_id: str, offset: int = 0, max_chars: int = 16000):
        return store.replay(snapshot_id).read(document_id, offset, max_chars)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        return store.run(run_id)

    @app.get("/api/runs/{run_id}/evaluation")
    def evaluate_saved_run(run_id: str):
        # Load trusted persisted observations; never accept contestant-supplied
        # traces or hidden reference panels through this public read endpoint.
        from .evaluator import evaluate
        saved = store.run(run_id)
        return evaluate(store.replay(saved["snapshot_id"]), saved)

    @app.get("/api/snapshots/{snapshot_id}/documents/{document_id}/find")
    def find(snapshot_id: str, document_id: str, q: str):
        return {"matches": store.replay(snapshot_id).find(document_id, q)}

    @app.get("/api/snapshots/{snapshot_id}/documents/{document_id}/follow")
    def follow(snapshot_id: str, document_id: str, url: str):
        return store.replay(snapshot_id).follow(document_id, url)

    @app.post("/api/compare")
    def comparison(body: Comparison):
        return compare(store, body.run_ids)

    @app.post("/api/snapshots/{snapshot_id}/sessions", status_code=201)
    def start_session(snapshot_id: str, body: SessionStart):
        return sessions.create(snapshot_id, **body.model_dump())

    @app.get("/api/sessions/{session_id}")
    def observe_session(session_id: str):
        return sessions.get(session_id)

    @app.post("/api/sessions/{session_id}/tools")
    def session_tool(session_id: str, body: SessionTool):
        # Arguments are data, not extra controller parameters.
        if any(key in body.arguments for key in ("session_id", "operation_key", "action")):
            raise SimError("invalid_arguments", "Tool arguments cannot override session control fields.")
        return sessions.tool(session_id, body.operation_key, body.action, **body.arguments)

    @app.post("/api/sessions/{session_id}/submit")
    def submit_session(session_id: str, body: SessionSubmit):
        return sessions.submit(session_id, **body.model_dump())

    @app.post("/api/sessions/{session_id}/reset", status_code=201)
    def reset_session(session_id: str, body: SessionReset):
        return sessions.reset(session_id, request_id=body.request_id)

    @app.post("/api/worlds/{world_id}/schedules", status_code=201)
    def create_schedule(world_id: str, body: ScheduleCreate):
        if scheduler_state["status"] != "running":
            raise SimError("scheduler_unavailable", "The scheduler is not running; inspect server state before creating work.", 503)
        return scheduler.create(world_id, **body.model_dump())

    @app.get("/api/schedules")
    def list_schedules(world_id: str | None = None):
        return {"schedules": scheduler.list(world_id=world_id), "runner": dict(scheduler_state)}

    @app.get("/api/schedules/{schedule_id}")
    def observe_schedule(schedule_id: str):
        return scheduler.status(schedule_id)

    @app.post("/api/schedules/{schedule_id}/pause")
    def pause_schedule(schedule_id: str, body: Empty):
        return scheduler.pause(schedule_id)

    @app.post("/api/schedules/{schedule_id}/resume")
    def resume_schedule(schedule_id: str, body: Empty):
        if scheduler_state["status"] != "running":
            raise SimError("scheduler_unavailable", "The scheduler is not running; inspect server state before resuming work.", 503)
        return scheduler.resume(schedule_id)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
