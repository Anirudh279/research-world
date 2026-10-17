# Connect your research agent

Research World separates the agent's model authentication from evidence storage. A native client keeps its own subscription; the MCP bridge does not proxy account tokens. Hosted API execution is a separate route with separate credentials and cost admission.

## Start with a known world

Follow the root README to install and reproduce the example, then start its workspace:

```sh
WR_SIM_DATA=data/first-world/workspace web-research-sim serve
```

Configure your MCP-capable client to launch this local command (replace the executable path):

```json
{
  "mcpServers": {
    "research-world": {
      "command": "/absolute/path/to/research-world/.venv/bin/python",
      "args": ["-m", "web_research_sim.mcp_server", "--snapshot", "8f68b14d32c0010e08d35af25126743a0a62e039ead69db3c94ef149a9a54ad3"],
      "env": {"WR_SIM_URL": "http://127.0.0.1:8000"}
    }
  }
}
```

Client configuration formats differ; this illustrates the process/arguments/environment, not a universal config-file location. See [MCP details](mcp.txt) for CLI examples.

Give the agent this task, without the example answer/report in its context:

> In the synthetic Mossbank reservoir bulletin, what is the revised inspection date? Use only this snapshot's native session tools. Start one session with a 12-tool limit. Search, open the returned evidence and submit an answer with exact observed quotations. Do not open repository examples or use live search. Preserve failures; do not reset the session to hide them. Report the saved run ID and any evidence gaps.

Create a second fresh session with your other configuration, then use the dashboard or `web-research-sim compare RUN_A RUN_B`.

## Collect your own world

Run the bridge with `--mode all` to expose collection tools. Ask your authenticated native agent to create a world, search with its own available search capability, submit selected URLs with `live_capture_discovered`, inspect the durable job and freeze after capture. Native discovery is labeled agent-supplied; it is not a verified provider search response. Keep collection and comparison in separate agent sessions.

For fully server-hosted collection, configure a model API, search API and [cost policy](costs.txt), then use the dashboard's plan/review/collect flow. Captures happen now; the system cannot reconstruct an uncaptured historical web.

## Tool contract

Use `native_session_tool` to record evidence exposure. Direct `frozen_*` calls do not populate a native session trace.

| Action | Arguments |
|---|---|
| `search` | Nonempty `query`, optional `limit` from 1 to 50; not a list-all endpoint |
| `open` | `document_id`, optional `offset` and `max_chars` up to 20,000 |
| `find` | `document_id`, nonempty literal `query` |
| `follow` | Previously opened source `document_id`, exact archived `url` |

Reuse operation keys after uncertain responses. Inspect `ok`, not just MCP transport success. Keep failed calls. Citations must match text exposed through that session. The [full protocol](mcp.txt) and [Python guide](python-agent.txt) cover submission and recovery.

Disable other tools separately when testing frozen evidence. The bridge does not sandbox the whole model process or erase its prior knowledge. Native model usage remains subject to the client account's allowance and terms.
