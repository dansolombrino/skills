---
description: Launching a sweep is sweep-dispatch's job, whatever follows it.
tags: [trigger]
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

Launch my 24-run learning rate sweep across rig-4090 and behemoth, one run per GPU at a time.
