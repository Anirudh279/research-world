# Research World

**The web changes. Your experiment shouldn't.**

Capture web evidence with an LLM. Freeze a research world. Compare agents against the same sources—with a trace of what each agent actually read.

[Use cases](#what-can-you-build-with-it) · [Quickstart](#try-it) · [Connect an agent](docs/agents.md) · [Methodology](docs/methodology.md) · [Validity study](docs/validity-study.md) · [Contribute](CONTRIBUTING.md)

![Research World: capture, freeze, compare](docs/assets/research-world.svg)

Built by [Anirudh Agarwal](https://github.com/Anirudh279) at **Small Theory Labs**. MIT licensed. **Development alpha**: useful for building and inspecting experiments; not a validated model leaderboard.

## Why this exists

Two agents can answer the same question and still take different tests. Search rankings move. Pages change. One agent sees a correction; the other sees an old article. A better answer may reflect better evidence rather than better reasoning.

Research World makes the evidence an explicit experimental input:

1. **Capture:** an LLM plans research, searches, selects sources and archives pages. You do not need an existing archive.
2. **Freeze:** a content-addressed snapshot binds source bytes and a cutoff.
3. **Compare:** agents search, open, find and follow within that snapshot. Answers retain their source-access traces and exact quotations.
4. **Inspect:** compare answers and evidence use. Export a portable challenge when you have rights to share its sources.

Use it to test a research prompt, debug a citation failure, examine what happens when evidence changes, or build the collection foundation for prospective forecasting studies.

## What can you build with it?

Research World gives research teams a shared evidence layer for experiments. The examples below are **illustrative workflows you can build**, not shipped financial benchmarks, trading integrations or validated investment strategies. Today, the environment provides collection, snapshots, research tools and traces; you supply domain-specific questions, outcomes and scoring.

### Stock research agents: test the thesis, not just the writing

**Example question:** “Before Northstar Software reports earnings, what evidence supports or weakens the claim that its operating margin will improve?” Northstar is a fictional company used to illustrate the experiment.

Capture the available earnings releases, investor presentations, product announcements and competitor commentary before the reporting date. Freeze that evidence. Ask one agent to build an investment thesis and another to actively seek disconfirming evidence, with the same sources and tool budget.

Compare whether they distinguish management guidance from reported results, trace claims back to the original source, and acknowledge gaps. An agent might cite growing revenue while overlooking an archived disclosure about rising infrastructure costs. The trace makes that omission inspectable.

**What you learn:** whether a prompt or agent change improves evidence use and thesis discipline. After earnings, an external evaluator can compare preregistered forecasts with the reported outcome. Research World does not supply market-price history, earnings labels or return calculations.

### Prediction-market agents: preserve what was knowable before resolution

**Example question:** “Will the fictional Port Alder terminal reopen by 30 June under the market's stated resolution rules?”

Collect the exact market rules, port-authority notices, repair updates and relevant reporting while the question is still open. Freeze a snapshot before resolution. Run a probability-estimating agent and a second configuration that explicitly checks deadlines, definitions and conflicting reports.

One agent may treat “repairs complete” as equivalent to “commercial operations resumed.” Another may notice that the resolution rules require the latter. Inspect which passages each agent actually received and which interpretation supports its answer. Later snapshots can show how newly captured evidence changes the forecast.

**What you learn:** whether the agent interprets the event correctly and updates its reasoning when evidence changes. Store probability submissions and resolution labels in your own evaluation harness; compute calibration or Brier scores there. Exchange connectivity, probability scoring and trading execution are not built in. A frozen archive also cannot remove future information already present in a model's training.

### What-if scenario analysis: change the evidence and inspect the response

**Example question:** “How would a two-week port closure change the supply-risk assessment for a manufacturer?”

Start with a captured baseline of supplier disclosures, logistics notices and company statements. For a controlled experiment, prepare a separate world containing a clearly labeled synthetic closure notice. Keep the original world intact and distinguish every scenario assumption from observed facts.

Ask the same agent configuration to research both worlds. Does it revise the affected supply-chain argument? Does it explain which conclusion changed because of the new notice? Does it invent a precise revenue impact despite having no inventory or shipment data? A third configuration could be tasked with challenging the scenario's assumptions.

**What you learn:** sensitivity of the agent's reasoning to specified evidence changes. Scenario construction currently requires your own experimental setup; there is no built-in scenario editor. These are tests of agent responses, not a causal economic simulator or a validated estimate of real-world impact.

### Hedge-fund research teams: make analyst-agent comparisons reviewable

**Example task:** “Prepare a bull case, bear case and unresolved-questions memo for a fictional industrial company before an investment committee meeting.”

Build a common research world from permitted public filings, company updates and industry sources. Give a generalist agent and a sector-focused configuration the same snapshot and declared limits. Ask both to identify catalysts, counterevidence and the facts that would change their conclusions.

A reviewer can inspect whether a claimed catalyst comes from a primary disclosure or repeated commentary, whether an important source was ever opened, and whether an unsupported assertion survives into the final memo. Separate frozen worlds can support experiments on how a new disclosure changes the memo without silently replacing the original evidence.

**What you learn:** which configuration produces more defensible research and where human review is needed. This can inform an internal research workflow, but portfolio construction, position sizing, licensed-data entitlements and P&L backtesting belong in your own systems. Production access controls and operational suitability require separate assessment.

### Research-agent developers: turn failures into regression cases

**Example task:** “A revised bulletin changed an inspection date. Does the agent cite the revision or repeat the superseded date?”

Use the included [Mossbank example](examples/mossbank/README.txt) to inspect saved real-agent runs on a self-authored fictional source. Connect your agent, start a fresh session and retain the passages it reads. When changing a prompt, retrieval policy or tool interface, compare the resulting answer and evidence trace against the same snapshot.

**What you learn:** whether a change fixes the observed failure without changing the underlying test. The saved report contains the answer, so keep it out of the agent's context during evaluation. This is the runnable starting point included in the repository; the domain workflows above extend the same pattern.

> **For prospective backtesting, start collecting before the event.** Research World preserves what you capture; it cannot recreate a historical page that was never archived. Keep later outcomes outside the earlier evidence snapshot, and distinguish evidence isolation from model-knowledge isolation.

## Try it

**Python 3.11+ · macOS or Linux**

```sh
git clone https://github.com/Anirudh279/research-world.git
cd research-world
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python examples/reproduce.py data/first-world
```

Open `data/first-world/comparison.html`. This verifies a saved comparison by two real native agents on a **fictional, project-authored source**. No API key is needed to inspect this example. It rechecks saved evidence; it does not generate new answers or establish which model is better.

Then run your own research:

```sh
web-research-sim serve
```

Open **http://127.0.0.1:8000**. Choose a question, configure a model route, collect sources and freeze a snapshot. Run both agents on that snapshot.

| Your route | What you supply | Start here |
|---|---|---|
| Native subscription agent | An authenticated local Codex or Claude Code client; it keeps its own authentication and uses MCP tools | [Agent guide](docs/agents.md) |
| Hosted APIs | Model key, search key and an explicit reviewed cost policy | [Setup](docs/setup.md) · [Costs](docs/costs.txt) |
| Docker | Docker Compose; configure either model route | `cp .env.example .env` then `docker compose up --build` |

Actual research uses an LLM. The key-free example is an inspection path, not a replacement for the model. Native subscriptions and API credits are separate; native inference remains outside the server's spending cap. Hosted adapters exist for OpenAI, Anthropic, Brave and Scrappa, but live verification is incomplete.

## Built for agent developers

- **HTTP + Python + MCP:** use your own agent loop or the supplied collection/research workflows.
- **Durable sessions:** tool observations, failed calls and submissions remain inspectable. Reusing operation keys supports recovery without silently repeating work.
- **Frozen tools:** missing evidence returns an archive miss, not a live-web fallback.
- **Originals and provenance:** preserve original bytes, extracted text, hashes and capture times.
- **Portable worlds:** export/import snapshots and standalone comparison reports.
- **Explicit limits:** request reservations and conservative hosted cost admission; unknown charges stay unknown.

[Python integration](docs/python-agent.txt) · [MCP contract](docs/mcp.txt) · [Storage](docs/storage-and-recovery.txt) · [Container deployment](docs/deployment.txt)

## What the evidence says

Our bounded validity study used one question, two original pages and two prompt strategies. Independent reconstruction reproduced the reported metrics. Controlled tests exercised future-evidence exclusion, source removal, contradiction, search order and corruption.

It also exposed failures: four original trials submitted no answer after invalid searches. We clarified the tool contract; both follow-up baseline trials completed. A blinded **model reviewer** checked claim support. This was not human validation, and it does not establish strategy superiority.

Read [the study and its limitations](docs/validity-study.md). The public repository includes a reproducible synthetic example; private study records and publisher archives are not distributed here.

## What this measures—and what it does not

| Can inspect | Cannot infer from it alone |
|---|---|
| Which archived passages a session received | All knowledge available to the model |
| Whether quotations match observed text | Whether every answer claim is correct |
| Whether runs use the same snapshot | Whether the whole agent is network-isolated |
| Tool calls and recorded reservations | Unknown native subscription costs |
| Captured evidence available at a cutoff | Historical pages we never captured |

This is an environment for constructing research benchmarks. It is not a complete copy of the internet, a browser task benchmark, or an established forecasting leaderboard. The product is an alpha; independent clean-host deployment, broader collection fidelity and live hosted-provider verification remain open.

## Help build the standard for inspectable research experiments

Start with a small contribution that another person can reproduce:

- Reproduce the example on a clean machine and report the exact environment.
- Add a self-authored challenge with a source correction, contradiction or missing fact.
- Connect an agent and share a trace, declared budget and failure analysis.
- Improve extraction with a minimal redistributable failing fixture.

[Contribution guide](CONTRIBUTING.md) · [Roadmap](docs/roadmap.md) · [Open an issue](https://github.com/Anirudh279/research-world/issues/new/choose)

If you use Research World in research, see [CITATION.cff](CITATION.cff). Please report the commit, snapshot digest and agent configuration—not just a score.

## Development

```sh
python -m pip install uv==0.7.4
uv sync --frozen
uv run --frozen pytest -q
```

Default tests use controlled fixtures. Dedicated CI jobs exercise containers and a local storage emulator without paid credentials. Passing them does not establish real provider compatibility.

## License

Code is [MIT](LICENSE). Project-authored synthetic starter text is CC0-1.0. Captured publisher content and dependencies retain their own rights; the code license does not grant permission to redistribute an archive. See [third-party notices](THIRD_PARTY_NOTICES.txt).
