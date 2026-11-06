This research project uses orchestrator mode: the main chat talks to the user, decides, and delegates substantial work to subagents through the host's subagent capability.
- The main chat keeps: conversation with the user, approvals, every `--confirm` command (`register`, `prune`, identity migrations), commits and tags, and the background watches on waves.
- Subagents take bounded work that would otherwise fill the main chat: experiment code, smoke tests, environment provisioning, plots and their screenshot checks, failure diagnosis, and large reads. Small tasks stay in the main chat.
- Running waves belong to the sweep-supervisor service, not to a chat or a subagent. Long jobs go through sweep-dispatch or tmux, because a subagent's commands can end with it.
- The supervisor's own agent ticks stay as they are: they must fire with no chat open.
- Rig, run-identity and environment commands run from the project's main checkout, never from an isolated worktree.
- One writer per checkout at a time: parallel subagents do not edit the same checkout or EXPERIMENTS.md.
