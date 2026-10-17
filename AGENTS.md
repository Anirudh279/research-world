# Working in Research World

Read README.md and docs/agents.md first. This file is for coding agents editing the repository; it is not research evidence.

- Install: `uv sync --frozen`. Test: `uv run --frozen pytest -q`.
- Never use live paid providers in default tests. Do not read or publish user credentials or workspace data.
- Preserve original capture bytes, capture/archive cutoffs and snapshot hashes.
- Do not retry ambiguous paid requests with a new key. Preserve durable reservation and operation records.
- A quote match is not semantic correctness; tool-scoped replay is not whole-agent isolation.
- Keep evaluation answers and comparison reports outside agent-accessible archives and runtime distributions.
- Python package and CLI names remain `web_research_sim` and `web-research-sim`.
- For integration, use the durable session protocol in docs/mcp.txt. Research agents should not read example answer files during evaluation.
- Validate packaging with scripts/check_distribution.py and inspect release scan findings. Never publish data/.
