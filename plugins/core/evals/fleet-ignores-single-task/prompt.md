---
description: One task with ordinary subagents is not a fleet.
tags: [trigger]
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

Fix the flaky login test in tests/test_login.py. Use subagents to dig through the CI logs if that helps.
