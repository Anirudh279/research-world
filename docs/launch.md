# Research World: launch note

By Anirudh Agarwal · Small Theory Labs

## The web changes. Your experiment shouldn't.

I built Research World to investigate a problem in evaluating research agents: when two agents answer the same question, they may be working from different evidence.

Research World captures sources, freezes a snapshot and lets agents research through tools bound to that snapshot. It records the passages each session received, so a comparison can be inspected instead of reduced to a citation count.

You can collect a new world with an LLM using hosted APIs or a supported native agent, connect your own research loop through HTTP/Python/MCP, and export a challenge when you have rights to share its sources.

The first lesson was uncomfortable and useful. A historical comparison wasn't fair: one agent had seen live discovery results the other hadn't. A fresh study then exposed an ambiguous search-tool contract. We kept those failures and documented the limits rather than manufacturing a leaderboard.

This release is a development alpha. The quickest way to explore it is a saved comparison on a self-authored fictional source. It takes no model key to inspect; running your own research uses an LLM. Independent deployment and broader provider/collection validation are still open.

I'm interested in developers who can reproduce it on a clean machine, researchers who can challenge the methodology, and agent builders who can contribute a small, inspectable failure case.

Repository: https://github.com/Anirudh279/research-world

## Short announcement draft

Research agents don't always take the same test: pages change, search rankings move, and one agent sees evidence another never receives.

I've open-sourced Research World: capture web sources with an LLM, freeze the evidence, and compare agents with inspectable source-access traces.

Python + Docker + MCP. MIT. Development alpha, with a runnable example and an honest account of what our first validity study did—and didn't—show.

I'd love clean-machine reproductions and small evidence challenges that break our assumptions.

https://github.com/Anirudh279/research-world
