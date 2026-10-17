# Research World

Local container startup: copy `.env.example` to `.env`, then run
`docker compose up --build`. Open `http://localhost:8000`. The archive persists
in the Compose named volume. Choose hosted API credentials and spending controls,
or connect your native subscription agent as described below. Container startup
and controlled import/replay checks passed; independent deployment and hosted
provider verification remain pending. See [first-session guidance](first-session.txt)
and [setup diagnostics](setup-diagnostics.txt).
For your own Linux server, use the [private deployment guide](deployment.txt),
which keeps the service on loopback and connects through SSH with a workspace token.

**Give an agent a question. Capture its sources. Hold the evidence still. Compare its research with another agent.**

Research World is an early implementation of an end-to-end web research simulation environment. You do not need to bring an archive. The supplied collection agent plans queries with an LLM, searches the web, selects sources and captures pages. A frozen snapshot then gives research agents the same evidence and cutoff.

**Development alpha, not a verified release.** Native Codex and Claude Code runs have exercised search, capture and frozen research; one Claude capture was partial, and a further live question had no usable sources. Hosted model/search API routes remain unverified live. Portable challenges, finite scheduling and container workflows are implemented, but independent deployment, a completed 24-hour soak, broader collection coverage and human usability still need evidence. Unit tests and single-account smoke runs do not satisfy those release gates.

## Inspect a real comparison

Open [the Mossbank report](../examples/mossbank/comparison.html) to see actual Codex and Claude Code runs over the same fictional source. The [example guide](../examples/mossbank/README.txt) includes the portable world and verification commands. Reading and verifying the saved report needs no model key; running your own agent does.

After installing, run `python examples/reproduce.py data/my-first-demo` from the
repository or unpacked source distribution. It imports the example into a new
local workspace, verifies its frozen bytes and recomputes the saved comparison.
Open the output `comparison.html` to inspect it. This short offline demonstration
replays saved evidence checks; it does not generate new agent answers. Choose an
unused output directory. The gallery is included in the source distribution,
but kept outside runtime images and the Python wheel.

## Start locally

Requires Python 3.11 or newer on macOS or Linux. The server currently uses a Unix process lock and supports one process per data directory.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

For hosted collection, configure one model provider and one search provider in your shell or secret manager. For native subscription research, follow [the MCP setup](mcp.txt); the agent keeps its own model authentication and can supply discovered URLs for capture without hosted model/search keys. The bare Python application does not load `.env` automatically; Docker Compose uses the supplied environment-file setup. Do not put credentials in a question, repository, command history or shared report.

| Capability | Environment variable | Current route |
|---|---|---|
| OpenAI model | `OPENAI_API_KEY` | Responses API |
| Anthropic model | `ANTHROPIC_API_KEY` | Messages API |
| Scrappa search | `SCRAPPA_API_KEY` | Search Light |
| Brave search | `BRAVE_API_KEY` | Web Search |

Before hosted collection or research, initialize a separate monetary allocation and install a reviewed `cost-policy.json` in the server data directory. See [cost configuration](costs.txt). Missing or expired bounds refuse transmission; credentials alone do not enable hosted calls. Native-client inference uses its own account and is outside this server cap.

The model ID is your choice and must be available to your account and compatible with the adapter's JSON responses. Having a ChatGPT or Claude subscription does **not** configure these API credentials or imply API credit.

```sh
web-research-sim serve
```

Open **http://127.0.0.1:8000**. Use the dashboard to:

1. Enter a question and request budgets.
2. Choose your configured model and search provider. Enter a model ID.
3. Plan research. Review the saved directions, queries and limits, then approve collection. Inspect captured sources and failures.
4. Freeze a snapshot after collection finishes.
5. Run a research agent, switch models, then run again on the same snapshot.
6. Select the two runs and compare their answers, traces and citation diagnostics.

You can also install a river, energy or civic starter directly in the dashboard. These clearly labeled synthetic worlds require no collection or model call to install; the default January 2 snapshot is selected for your first explicit research run. See [starter worlds](starters.txt).

The dashboard shows credential presence, not a verified connection. It makes no provider call until you explicitly start collection or research. Source text is displayed as text, not executable HTML. Saved captures show HTTP and extraction status separately; a saved original does not imply usable evidence.

## The same flow from the CLI

The CLI connects to the running server. Substitute IDs returned by each command; placeholders below are not valid IDs.

```sh
web-research-sim doctor
web-research-sim create "What factors influence European electricity demand this winter?" \
  --model-calls 18 --search-calls 5 --capture-calls 20
web-research-sim plan WORLD_ID --provider openai --model YOUR_MODEL_ID \
  --search-provider brave --max-queries 3 --max-pages 5 --wait
# Review the completed plan job's directions and queries before approving it.
web-research-sim collect WORLD_ID --plan-job PLAN_JOB_ID --provider openai --model YOUR_MODEL_ID \
  --search-provider brave --max-queries 3 --max-pages 5 --wait
web-research-sim status WORLD_ID
web-research-sim freeze WORLD_ID
web-research-sim research SNAPSHOT_ID --provider openai --model YOUR_MODEL_ID --wait
web-research-sim research SNAPSHOT_ID --provider anthropic --model YOUR_OTHER_MODEL_ID --wait
web-research-sim report RUN_ID
web-research-sim compare FIRST_RUN_ID SECOND_RUN_ID
```

Collection normally reserves two model calls, up to three search calls and five page captures with these settings. Each research run can reserve up to eight model calls. The example's 18 model calls permit those two complete research runs after collection. **Call budgets are not dollar caps.** Provider charges vary and may apply even when a response fails. Uncertain requests remain counted and are never automatically retried.

Planning uses one of those model calls and makes no search or capture request.
Approval reuses the saved plan; it does not pay for planning again. Keep the same
provider/model/limit settings when approving. A plan is bound to one collection
job; inspect that job after an uncertain response. The explicit CLI/API collect
route without a plan still performs automatic planning and collection for
unattended workflows. See [plan review](collection-plans.txt).

If polling times out or the connection drops, inspect the returned job ID before starting anything else:

```sh
web-research-sim job JOB_ID
```

A stopped server marks previously queued or running jobs `interrupted` on restart. It does not resume or repeat their paid requests. Existing evidence and reservations remain in the workspace.

## Developer access

Interactive HTTP documentation is at `/docs`. See [the Python agent guide](python-agent.txt) for durable research sessions, observed citations and recovery. The Python client uses the same routes:

```python
from web_research_sim.client import Client

with Client("http://127.0.0.1:8000") as client:
    status = client.get("/api/status")
    world = client.post("/api/worlds", {
        "question": "Your research question",
        "budgets": {"model_calls": 18, "search_calls": 5, "capture_calls": 20},
    })
```

The server stores SQLite metadata and content-addressed objects under `data/`, or the directory selected with `WR_SIM_DATA`. Raw source content and model responses can contain sensitive or copyrighted material, so do not publish this directory wholesale.

## Share a frozen challenge

Export only sources you have permission to redistribute. The package includes
the frozen manifest and its source/text objects; it excludes the workspace database,
credentials and unrelated model responses. Source text and provenance can still
contain sensitive information and require review before sharing.

```sh
web-research-sim export SNAPSHOT_ID challenge.zip --data data --allow-redistribution
web-research-sim import challenge.zip --data imported-data
WR_SIM_DATA=imported-data web-research-sim serve
```

The imported snapshot retains its digest and tool responses. Imported worlds
start with zero live-call allowance. To run a model, explicitly configure its
credentials and allocation, for example:

```sh
web-research-sim budgets WORLD_ID --model-calls 16 --search-calls 0 --capture-calls 0
web-research-sim compare RUN_A RUN_B --html comparison.html
```

Call limits are absolute totals, including past reservations, not dollar ceilings.
The standalone HTML report needs no server. It includes answers and traces, so
review it before sharing. Use `export --metadata-only` if content cannot be
redistributed; that package cannot reproduce the world and its import is refused.
The export/import commands access a local data directory; for container volumes,
execute them inside the container using `python -m web_research_sim.cli`.

Collection and research commands print a request key before submission. After
an uncertain connection failure, repeat the identical command with
`--request-key ORIGINAL_KEY` to observe the original job, without submitting it again.

The client accepts `WR_SIM_URL` and `WR_SIM_TOKEN`. Remote API access requires a configured `WR_SIM_PUBLIC_HOST` and `WR_SIM_TOKEN`; authentication is bearer-based. The dashboard now accepts the workspace token through its Connect form, keeps it only in tab memory, and clears it on disconnect or reload. This is not a model-provider credential field. Use HTTPS for remote access; independent remote deployment and TLS setup remain unverified. The default server binds to loopback.

## What is trustworthy today—and what is still missing

Implemented mechanisms include capture timestamps, content hashes, explicit call reservations, frozen search/open tools, source-access traces and exact-quote citation validation. Comparisons require the same snapshot. These mechanisms still need the broader release verification suite.

An exact quote does not prove an answer is correct. Citation count does not measure research quality. Model costs shown as unknown are not zero. Archive-only application tools do not establish full process/network isolation or prevent a model from relying on prior knowledge.

Still to build or verify:

- Broader verification of native subscription workflows: Codex and Claude Code have completed live search/capture smoke runs and frozen research through our tools. These are tool-scoped runs, not whole-agent network isolation. No subscription credential proxying; see `docs/mcp.txt`.
- Independent clean Linux deployment and fresh macOS setup (local container smoke passed).
- Completion of the running real 24-hour restart soak (finite scheduling, pause/resume and restart recovery are implemented; see `docs/scheduling.txt`).
- Automatic rendered-page selection and OCR. Explicit host-side `capture-rendered` now uses an inspected offline Docker browser, preserves source assets and derived DOM, and supports portable frozen snapshots. Controlled Docker tests and a live public-page capture passed; subresource redirects remain explicit gaps. See `docs/rendered-capture.txt`.
- Independent clean-install reproduction of exported challenges (fixture round trips pass).
- Provider billing reconciliation and semantic-evaluation validation. Twelve synthetic known-answer tasks and deterministic evidence checks exist, but they do not establish semantic-judge accuracy or real-world research quality. Hosted API admission supports conservative operator-reviewed money bounds; unknown charges retain their reservations.
- Clean-machine onboarding, live hosted-provider tests and independent developer usability evidence. Automated mobile browser checks exercised cost status, starter installation and capture diagnostics; they are not human walkthroughs.

## Tests

```sh
python -m pip install pytest
python -m pytest -q
```

Provider and HTTP transport tests use controlled responses. They test implementation contracts, **not** live account access, real provider costs or subscription compatibility. See `docs/provider-capabilities.txt` for adapter and capture limitations.

## License

Product code: [MIT](../LICENSE). Self-authored synthetic starter text: CC0-1.0. Captured publisher content and third-party dependencies retain their own rights and licenses; exporting a world requires appropriate source permissions.
