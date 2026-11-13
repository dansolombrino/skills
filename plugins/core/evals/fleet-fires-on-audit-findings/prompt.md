---
description: Several findings to fix in parallel by separate agents is a fleet.
tags: [trigger]
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

The audit in docs/audit.md has six findings. Get them fixed in parallel: one agent per finding, each on its own branch, all reporting back to you, and you bring me the decisions.
