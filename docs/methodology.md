# A research benchmark is an evidence experiment

By Anirudh Agarwal · Small Theory Labs

A live-web comparison mixes two questions: how well did an agent reason, and what information did it happen to find? Both matter, but they are different experimental variables.

Research World separates collection from frozen research. Collection uses an LLM to plan and discover sources; freezing records the available archive; research runs receive tools bound to that snapshot. This lets a developer inspect evidence differences instead of hiding them in an aggregate score.

## Five commitments

1. **Availability is recorded, not guessed.** A publication date does not prove we had a page at that time. Capture and archive timestamps constrain snapshot membership.
2. **Observation is distinct from existence.** A page in the archive is not necessarily a page the agent read. Record the actual returned text windows.
3. **Quote matching is not truth.** A quotation can be real and fail to support the answer. Semantic review is a separate step.
4. **Failure is a result.** Preserve missing answers, archive misses, invalid calls and uncertain costs. Do not silently rerun until success.
5. **Verification should survive a second implementation.** Test the reported measurements against raw bytes and records, not only the same functions that produced them.

## Design a useful experiment

Choose a narrow question. Declare the agents, evidence snapshot, tools, budgets and scoring before running. Decide what would invalidate your conclusion. Use fresh sessions; control external tools separately. Keep answer keys out of agent-accessible archives.

Then change one thing: remove a source, add a contradictory document, vary ordering or attempt to introduce later evidence. Inspect whether the failure belongs to discovery, capture, extraction, retrieval or reasoning. Keep uncertainty when the trace cannot distinguish them.

Our [first validity study](validity-study.md) found a hidden historical information imbalance and an ambiguous tool contract. Those are useful findings even though they cannot produce a leaderboard.

## Relationship to adjacent projects

[BrowserGym](https://github.com/ServiceNow/BrowserGym) provides a common environment/interface for web-agent tasks. [WebArena](https://github.com/web-arena-x/webarena) provides realistic website environments and task evaluation. [Browser Use](https://github.com/browser-use/browser-use) helps agents operate browsers.

Research World focuses on captured research evidence: collecting it, freezing it and tracing how an agent uses it. These projects informed our emphasis on a clear interface, a concrete example and explicit evaluation scope. No compatibility or affiliation is implied.

## What would change our confidence?

Independent clean-machine reproductions, challenging redistributable worlds, failures with minimal traces, and independent semantic judgments. A rising citation count or a polished demo alone would not.
