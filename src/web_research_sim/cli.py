"""Command-line access to the same API used by the dashboard."""
import argparse
import json
import sys
import time
import uuid
from urllib.parse import quote
import httpx
from .client import Client, APIError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a research world, freeze evidence, and compare agents.")
    parser.add_argument("--url", help="Server URL (or WR_SIM_URL)")
    commands = parser.add_subparsers(dest="command", required=True)
    isolated = commands.add_parser("research-isolated", help="Host model API calls with Docker network-disabled frozen tools")
    isolated.add_argument("snapshot")
    isolated.add_argument("--data", default="data")
    isolated.add_argument("--image-id", required=True)
    isolated.add_argument("--request-key", required=True)
    isolated.add_argument("--provider", choices=["openai", "anthropic"], required=True)
    isolated.add_argument("--model", required=True)
    isolated.add_argument("--max-steps", type=int, default=8)
    serve = commands.add_parser("serve", help="Start the local dashboard and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("doctor", help="Check server and provider configuration")
    preflight = commands.add_parser("preflight", help="Check local Docker, port and storage without provider calls")
    preflight.add_argument("--data", default="data")
    preflight.add_argument("--port", type=int, default=8000)
    costs = commands.add_parser("costs", help="Inspect or initialize an immutable hosted API allocation")
    costs.add_argument("--data", default="data")
    costs.add_argument("--allocate-micro-usd", type=int, help="One-time allocation; 1000000 units = USD 1")
    rendered = commands.add_parser("capture-rendered", help="Capture a selected live page using an isolated local Docker browser")
    rendered.add_argument("world")
    rendered.add_argument("source_url")
    rendered.add_argument("--data", default="data", help="The existing world's local storage directory")
    rendered.add_argument("--image-id", required=True, help="Exact locally installed renderer image ID: sha256:...")
    rendered.add_argument("--request-key", required=True, help="Unique operation key; retain it after an uncertain result")
    starter = commands.add_parser("starter", help="Install synthetic evidence for a real LLM research session")
    starter.add_argument("name", choices=["list", "river", "energy", "civic"])
    starter.add_argument("--data", default="data")
    evaluation = commands.add_parser("evaluate", help="Evaluate a saved run locally; hidden references stay outside agent tools")
    evaluation.add_argument("run")
    evaluation.add_argument("--data", default="data")
    evaluation.add_argument("--references", help="Evaluator-only JSON list of {id,url,quote} reference items")
    evaluation.add_argument("--output", help="Write a new evaluation JSON file")
    integrity = commands.add_parser("verify-world", help="Verify every original, text and asset referenced by a frozen manifest")
    integrity.add_argument("snapshot")
    integrity.add_argument("--data", default="data")
    verify = commands.add_parser("verify-comparison", help="Reproduce saved comparison metrics against an imported frozen world")
    verify.add_argument("comparison", help="JSON comparison saved by the compare command")
    verify.add_argument("--data", default="data")
    export = commands.add_parser("export", help="Export a frozen challenge from a local workspace")
    export.add_argument("snapshot")
    export.add_argument("output")
    export.add_argument("--data", default="data")
    export.add_argument("--allow-redistribution", action="store_true", help="Confirm you have permission to redistribute all included sources")
    export.add_argument("--metadata-only", action="store_true", help="Omit source content; cannot reproduce the frozen world")
    imported = commands.add_parser("import", help="Validate and import a portable challenge into a local workspace")
    imported.add_argument("package")
    imported.add_argument("--data", default="data")
    create = commands.add_parser("create", help="Create a world with explicit spending limits")
    create.add_argument("question")
    budgets = commands.add_parser("budgets", help="Set absolute call limits; does not reset spent calls or enforce dollars")
    budgets.add_argument("world")
    for name in ("model", "search", "capture"):
        budgets.add_argument(f"--{name}-calls", type=int, required=True)
    for name, default in (("model", 18), ("search", 5), ("capture", 20)):
        create.add_argument(f"--{name}-calls", type=int, default=default)
    for name, identity in (("plan", "world"), ("collect", "world"), ("freeze", "world"), ("research", "snapshot"), ("status", "world"), ("report", "run"), ("job", "job")):
        command = commands.add_parser(name)
        command.add_argument(identity)
        if name in ("plan", "collect", "research"):
            command.add_argument("--request-key", help="Reuse this key after an uncertain submission to observe the original job")
            command.add_argument("--provider", choices=["openai", "anthropic"], required=True)
            command.add_argument("--model", required=True, help="Model ID available to your provider account")
            command.add_argument("--wait", action="store_true", help="Poll this submitted job until it finishes; never resubmit")
            command.add_argument("--wait-seconds", type=int, default=900)
        if name in ("plan", "collect"):
            command.add_argument("--search-provider", choices=["scrappa", "brave"], required=True)
            command.add_argument("--max-queries", type=int, default=3)
            command.add_argument("--max-pages", type=int, default=5)
        if name == "collect":
            command.add_argument("--plan-job", help="Approve this completed saved plan; all provider and limit settings must match")
        if name == "research":
            command.add_argument("--max-steps", type=int, default=8)
    compare = commands.add_parser("compare")
    compare.add_argument("runs", nargs="+", help="Two or more completed run IDs")
    compare.add_argument("--html", help="Write an inert, standalone comparison report to this file")
    native = commands.add_parser("session", help="Trace a native agent against frozen tools; does not isolate the agent's network")
    session_commands = native.add_subparsers(dest="session_command", required=True)
    start = session_commands.add_parser("start")
    start.add_argument("snapshot")
    start.add_argument("--provider", required=True, help="Declared native client/provider; identity is not verified")
    start.add_argument("--model", required=True, help="Declared model identity; not verified")
    start.add_argument("--tool-limit", type=int, default=30)
    start.add_argument("--request-key", help="Stable key for this session creation")
    observe = session_commands.add_parser("status")
    observe.add_argument("session_id")
    tool = session_commands.add_parser("tool")
    tool.add_argument("session_id")
    tool.add_argument("action", choices=["search", "open", "find", "follow"])
    tool.add_argument("--arguments", default="{}", help="JSON tool arguments; source text is untrusted")
    tool.add_argument("--request-key", help="Stable key for this exact tool call")
    submit_session = session_commands.add_parser("submit")
    submit_session.add_argument("session_id")
    submit_session.add_argument("--file", required=True, help="JSON file with answer and citations; exact repeats return the same run")
    reset = session_commands.add_parser("reset", help="Create a separate session, preserving the old trace")
    reset.add_argument("session_id")
    reset.add_argument("--request-key", help="Stable key for this reset")
    schedule = commands.add_parser("schedule", help="Run finite collection schedules while the server stays running")
    schedule_commands = schedule.add_subparsers(dest="schedule_command", required=True)
    planned = schedule_commands.add_parser("create")
    planned.add_argument("world")
    planned.add_argument("--kind", choices=["collection", "native_discovered"], required=True)
    planned.add_argument("--config", required=True, help="JSON config file: explicit providers or native-discovered source URLs")
    planned.add_argument("--interval-seconds", type=int, required=True)
    planned.add_argument("--max-runs", type=int, required=True)
    planned.add_argument("--start-at", required=True, help="ISO 8601 timestamp with timezone")
    planned.add_argument("--request-key", help="Stable creation key; reuse after uncertain response")
    listed = schedule_commands.add_parser("list")
    listed.add_argument("--world")
    for action in ("status", "pause", "resume"):
        operation = schedule_commands.add_parser(action)
        operation.add_argument("schedule_id")
    args = parser.parse_args(argv)
    if args.command == "research-isolated":
        from .core import Store, SimError
        from .isolated_run import run_once
        try:
            result = run_once(Store(args.data), args.snapshot, request_key=args.request_key,
                              provider=args.provider, model=args.model, image_id=args.image_id, max_steps=args.max_steps)
        except SimError as exc:
            print(f"Error ({exc.code}): {exc.message}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "completed" else 1
    if args.command == "verify-world":
        from .core import Store
        from .integrity import verify_snapshot
        result = verify_snapshot(Store(args.data).objects, args.snapshot)
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "verify-comparison":
        from pathlib import Path
        from .core import Store, SimError
        from .comparison_verify import verify_comparison
        try:
            with Path(args.comparison).open('rb') as source:
                raw = source.read(16 * 1024**2 + 1)
            if len(raw) > 16 * 1024**2:
                raise ValueError('Comparison exceeds 16 MiB.')
            result = verify_comparison(Store(args.data), json.loads(raw))
        except (OSError, ValueError, SimError):
            print('Comparison verification failed: check the JSON file and imported snapshot.', file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result['matches'] else 1
    if args.command == "capture-rendered":
        from .core import Store, SimError
        from .capture import CaptureError
        from .render_archive import capture_rendered
        try:
            result = capture_rendered(Store(args.data), args.world, args.source_url,
                                      operation_key=args.request_key, image_id=args.image_id)
        except (SimError, CaptureError, OSError) as exc:
            code = exc.code if isinstance(exc, SimError) else type(exc).__name__
            print(f"Rendered capture did not complete ({code}). Inspect the saved operation before any new attempt.", file=sys.stderr)
            return 1
        print(json.dumps({"document_id": result["id"], "url": result["url"],
                          "status": result["extraction_status"], "artifacts": len(result.get("artifacts", [])),
                          "request_key": args.request_key}, indent=2))
        return 0
    if args.command == "costs":
        from .costs import CostLedger
        from .core import SimError
        try:
            ledger = CostLedger(args.data)
            result = ledger.summary() if args.allocate_micro_usd is None else ledger.allocate(args.allocate_micro_usd)
        except SimError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "preflight":
        from .preflight import preflight
        try:
            result = preflight(args.data, args.port)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
        return 1 if any(c["status"] == "unavailable" for c in result["checks"]) else 0
    if args.command == "starter":
        from .starters import list_starters, install_starter
        from .core import Store
        result = list_starters() if args.name == "list" else install_starter(Store(args.data), args.name)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "evaluate":
        from pathlib import Path
        from .core import Store, SimError
        from .evaluator import evaluate
        try:
            refs = []
            if args.references:
                with Path(args.references).open("rb") as source:
                    raw = source.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("Reference file exceeds 1 MiB")
                refs = json.loads(raw)
            store = Store(args.data)
            run = store.run(args.run)
            result = evaluate(store.replay(run["snapshot_id"]), run, refs)
            if args.output:
                with Path(args.output).open("x", encoding="utf-8") as out:
                    json.dump(result, out, indent=2)
            print(json.dumps(result, indent=2))
            return 0
        except (OSError, ValueError, SimError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    if args.command in ("export", "import"):
        from pathlib import Path
        from .core import Store, SimError
        from .bundles import export_snapshot, import_snapshot, MAX_TOTAL, MAX_ENTRIES
        try:
            store = Store(args.data)
            if args.command == "export":
                payload = export_snapshot(store, args.snapshot, redistribution_acknowledged=args.allow_redistribution, metadata_only=args.metadata_only)
                with Path(args.output).open("xb") as output:
                    output.write(payload)
                result = {"path": args.output, "snapshot_id": args.snapshot, "reproducible": not args.metadata_only}
            else:
                with Path(args.package).open("rb") as source:
                    payload = source.read(MAX_TOTAL + MAX_ENTRIES * 300 + 1)
                result = import_snapshot(store, payload)
            print(json.dumps(result, indent=2))
            return 0
        except (SimError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    if args.command == "serve":
        import uvicorn
        uvicorn.run("web_research_sim.api:create_app", factory=True, host=args.host, port=args.port)
        return 0
    encode = lambda value: quote(value, safe="")
    request_key = None
    if args.command in ("plan", "collect", "research") or (args.command == "session" and args.session_command in ("start", "tool", "reset")) or (args.command == "schedule" and args.schedule_command == "create"):
        request_key = args.request_key or uuid.uuid4().hex
        print(f"Request key: {request_key}. Keep this key if the connection is interrupted.", file=sys.stderr)
    try:
        with Client(base_url=args.url) as client:
            if args.command == "doctor":
                result = client.get("/api/status")
            elif args.command == "schedule":
                if args.schedule_command == "create":
                    from pathlib import Path
                    with Path(args.config).open("rb") as source:
                        raw_config = source.read(128 * 1024 + 1)
                    if len(raw_config) > 128 * 1024:
                        raise ValueError("Schedule configuration exceeds 128 KiB.")
                    config = json.loads(raw_config)
                    if not isinstance(config, dict):
                        raise ValueError("Schedule configuration must be a JSON object.")
                    result = client.post(f"/api/worlds/{encode(args.world)}/schedules", {"kind": args.kind, "config": config, "interval_seconds": args.interval_seconds, "max_runs": args.max_runs, "start_at": args.start_at, "request_id": request_key})
                elif args.schedule_command == "list":
                    result = client.get("/api/schedules", params={"world_id": args.world} if args.world else {})
                elif args.schedule_command == "status":
                    result = client.get(f"/api/schedules/{encode(args.schedule_id)}")
                else:
                    result = client.post(f"/api/schedules/{encode(args.schedule_id)}/{args.schedule_command}")
            elif args.command == "session":
                if args.session_command == "start":
                    result = client.post(f"/api/snapshots/{encode(args.snapshot)}/sessions", {"provider": args.provider, "model": args.model, "tool_limit": args.tool_limit, "request_id": request_key})
                elif args.session_command == "status":
                    result = client.get(f"/api/sessions/{encode(args.session_id)}")
                elif args.session_command == "tool":
                    arguments = json.loads(args.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object.")
                    result = client.post(f"/api/sessions/{encode(args.session_id)}/tools", {"operation_key": request_key, "action": args.action, "arguments": arguments})
                elif args.session_command == "reset":
                    result = client.post(f"/api/sessions/{encode(args.session_id)}/reset", {"request_id": request_key})
                else:
                    from pathlib import Path
                    with Path(args.file).open("rb") as source:
                        payload = source.read(128 * 1024 + 1)
                    if len(payload) > 128 * 1024:
                        raise ValueError("Submission file exceeds 128 KiB.")
                    submission = json.loads(payload)
                    if not isinstance(submission, dict):
                        raise ValueError("Submission must be a JSON object with answer and citations.")
                    result = client.post(f"/api/sessions/{encode(args.session_id)}/submit", submission)
            elif args.command == "create":
                result = client.post("/api/worlds", {"question": args.question, "budgets": {k + "_calls": getattr(args, k + "_calls") for k in ("model", "search", "capture")}})
            elif args.command == "budgets":
                result = client.post(f"/api/worlds/{encode(args.world)}/budgets", {k + "_calls": getattr(args, k + "_calls") for k in ("model", "search", "capture")})
            elif args.command in ("plan", "collect"):
                payload = {"model_provider": args.provider, "model": args.model, "search_provider": args.search_provider, "max_queries": args.max_queries, "max_pages": args.max_pages}
                if args.command == "collect" and args.plan_job:
                    payload["plan_job_id"] = args.plan_job
                result = client.post(f"/api/worlds/{encode(args.world)}/{args.command}", payload, idempotency_key=request_key)
            elif args.command == "freeze":
                result = client.post(f"/api/worlds/{encode(args.world)}/freeze")
            elif args.command == "research":
                result = client.post(f"/api/snapshots/{encode(args.snapshot)}/research", {"model_provider": args.provider, "model": args.model, "max_steps": args.max_steps}, idempotency_key=request_key)
            elif args.command == "status":
                result = client.get(f"/api/worlds/{encode(args.world)}")
            elif args.command == "job":
                result = client.get(f"/api/jobs/{encode(args.job)}")
            elif args.command == "report":
                result = client.get(f"/api/runs/{encode(args.run)}")
            else:
                if len(args.runs) < 2:
                    parser.error("compare requires at least two run IDs")
                result = client.post("/api/compare", {"run_ids": args.runs})
                if args.html:
                    from pathlib import Path
                    from .report import render_report
                    with Path(args.html).open("x", encoding="utf-8") as output:
                        output.write(render_report(result))
            if getattr(args, "wait", False):
                job = result.get("job", result)
                deadline = time.monotonic() + args.wait_seconds
                print(f"Submitted job {job['id']}; waiting without resubmission.", file=sys.stderr)
                # Idempotent POST replies are handles, even if the prior job
                # already completed. Read its durable result rather than lose it.
                if job["status"] not in ("queued", "pending", "running") and "result" not in job:
                    job = client.get(f"/api/jobs/{encode(job['id'])}")
                while job["status"] in ("queued", "pending", "running"):
                    if time.monotonic() >= deadline:
                        print(json.dumps(job, indent=2))
                        print("Wait expired; the job may still be running. Inspect its job ID before taking further action.", file=sys.stderr)
                        return 2
                    time.sleep(2)
                    job = client.get(f"/api/jobs/{encode(job['id'])}")
                result = job
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 1 if result.get("status") in ("failed", "error", "cancelled", "interrupted", "invalid_citations") or result.get("ok") is False else 0
    except (APIError, httpx.HTTPError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
