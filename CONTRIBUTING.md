# Contributing to Research World

The most valuable contribution is a result someone else can reproduce.

Start with the README example. For development:

```sh
python -m pip install uv==0.7.4
uv sync --frozen
uv run --frozen pytest -q
```

Do not configure paid provider credentials for tests. Container and S3-emulator jobs are separate integrations. See [implementation guidance](docs/contributing.txt).

## Good first contributions

- **Clean-machine reproduction:** OS, Python/Docker versions, exact command, expected and actual result. Redact paths and secrets.
- **A small evidence challenge:** self-authored source text, explicit rights, a question and a change that makes reasoning interesting. Keep answers outside agent-accessible data.
- **An extraction failure:** a minimal redistributable HTML/PDF fixture and the lost fact.
- **An agent integration:** a fresh session trace and honest account of external tools and unknown costs.

Discuss new providers or large architecture changes in an issue first. Fixes should preserve source bytes, snapshot identity, idempotency and reservation accounting. Add focused negative tests for changes to these boundaries.

Never commit `.env`, workspace databases, account transcripts, captured publisher archives or real credentials. Synthetic data is preferred for regression tests. Use the release scanner and distribution checks before proposing artifacts.

A contribution is ready when its claims match its evidence. Do not rename a failed run, silently change frozen labels or describe fixture tests as live verification.
