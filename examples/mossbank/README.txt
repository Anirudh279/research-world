Mossbank: inspect a real agent comparison

Open comparison.html in a browser. It is a standalone report: no account, server,
JavaScript or network is needed to read it. Two real native agents (Codex and
Claude Code) researched the same self-authored fictional bulletin on 15 October
2026. Both answered with an observed quote. The sources are synthetic; the model
runs are real. This is a tiny integration example, not a model ranking benchmark.

Inspect the trace: each agent searches, opens the evidence, and submits its answer.
Compare the reported source access, citations, model labels and unknown costs.
Recorded trace steps count saved tool operations, not all native inference calls.
The historical JSON field model_steps retains that same trace-count meaning.
The earlier planned date differs from the revised inspection date. The archive
holds the evidence fixed so you can inspect whether each answer cites the revision.

Reproduce saved evidence checks from the repository root after installing:

  web-research-sim import examples/mossbank/world.zip --data demo-recipient
  web-research-sim verify-world 8f68b14d32c0010e08d35af25126743a0a62e039ead69db3c94ef149a9a54ad3 --data demo-recipient
  web-research-sim verify-comparison examples/mossbank/comparison.json --data demo-recipient

These commands verify saved artifacts; they do not rerun a model. For a fresh
agent run, start WR_SIM_DATA=demo-recipient web-research-sim serve and connect
your authenticated native agent using docs/mcp.txt or docs/python-agent.txt.
Pin the snapshot above, create a new session, research and submit exact quotes.
Keep your new run separate from the historical examples. Fresh generations can
vary. Imported worlds have zero live-call budgets; native session reasoning stays
in your client and still consumes that client's plan allowance.

Question: In the synthetic Mossbank reservoir bulletin, what is the revised
inspection date?

Trust boundaries: external native clients were tool-scoped, not fully isolated.
The model names are declared and unverified. Actual subscription monetary costs
are unknown. Quote validity does not prove semantic support. The JSON/HTML records
are inspectable examples, not authenticated provider attestations. The package
contains one fictional source and no provider credentials or private raw CLI logs.

Rights: synthetic source text is project-authored and released CC0-1.0. This
example does not grant rights to any publisher pages collected in other worlds.
The report's boilerplate/code is MIT. Keep the comparison outside agent-accessible
archives when evaluating this challenge; it contains the answers.
