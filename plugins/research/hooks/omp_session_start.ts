/**
 * The SessionStart hooks of hooks.json, for omp, which runs extensions instead of command hooks.
 *
 * On each event omp gives for a session starting, resuming, being cleared, forked or compacted, it
 * runs the same two scripts with the same contract (the hook payload on stdin, CLAUDE_PLUGIN_ROOT
 * and CLAUDE_PROJECT_DIR in the environment, the session's directory as cwd, 5 s each) and hands
 * what they print to the model once, as a hidden message. The scripts decide whether the project is
 * tagged research; when they print nothing, so does this. Subagent sessions get nothing, as in a
 * host whose SessionStart hooks fire only for the main session.
 *
 * A new, resumed or forked session gets the message with its next prompt: omp replaces a resumed
 * session's messages after `session_switch`, so a message sent from that event would never reach
 * the model, and a later event before that prompt replaces the earlier one. A compaction can
 * happen mid-run, so its message goes straight into the next turn.
 */
import { type ChildProcess, spawn } from "node:child_process";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";

const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCRIPTS = ["hooks/orchestration_note.py", "skills/sweep-supervisor/scripts/wave_reminder.py"];
const TIMEOUT_MS = 5_000;
const MESSAGE_TYPE = "research-session-start";

type Source = "startup" | "resume" | "clear" | "compact" | "fork";

/** What a SessionStart hook's stdout adds to the context: its `additionalContext`, or plain text. */
function contextOf(stdout: string): string {
	const text = stdout.trim();
	if (!text) return "";
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		return text;
	}
	const context = (parsed as { hookSpecificOutput?: { additionalContext?: unknown } } | null)?.hookSpecificOutput
		?.additionalContext;
	return typeof context === "string" ? context.trim() : "";
}

/** One script's context, or "" on any failure, non-zero exit or timeout: a hook never breaks a session. */
function runScript(script: string, payload: object, cwd: string): Promise<string> {
	const { promise, resolve } = Promise.withResolvers<string>();
	let child: ChildProcess;
	try {
		child = spawn("python3", [path.join(PLUGIN_ROOT, script)], {
			cwd,
			env: { ...process.env, CLAUDE_PLUGIN_ROOT: PLUGIN_ROOT, CLAUDE_PROJECT_DIR: cwd },
			stdio: ["pipe", "pipe", "ignore"],
		});
	} catch {
		return Promise.resolve("");
	}
	let stdout = "";
	const timer = setTimeout(() => {
		try {
			child.kill("SIGKILL");
		} catch {}
	}, TIMEOUT_MS);
	child.stdout?.on("data", chunk => {
		stdout += chunk;
	});
	child.on("error", () => {
		clearTimeout(timer);
		resolve("");
	});
	child.on("close", code => {
		clearTimeout(timer);
		resolve(code === 0 ? contextOf(stdout) : "");
	});
	child.stdin?.on("error", () => {});
	child.stdin?.end(JSON.stringify(payload));
	return promise;
}

/** Framed as the session-start hook context it is: the model gets it as a user-role message, after a prompt. */
function customMessage(content: string) {
	const framed = `<system-reminder>\nSessionStart hook additional context: ${content}\n</system-reminder>`;
	return { customType: MESSAGE_TYPE, content: framed, display: false, attribution: "agent" as const };
}

export default function researchSessionStart(pi: ExtensionAPI) {
	let pending: Promise<string> | undefined; // context waiting for this session's next prompt
	let offered: Promise<string> | undefined; // the pending context a prompt being prepared carries

	/** Never rejects: a pending promise nobody awaits must not fail, and a hook never breaks a session. */
	async function gather(source: Source | "launch", ctx: ExtensionContext): Promise<string> {
		try {
			if (ctx.agent?.kind === "sub") return "";
			// A session opened at launch is a resume when it already holds a conversation (--continue, --resume).
			if (source === "launch")
				source = ctx.sessionManager.getBranch().some(entry => entry.type === "message") ? "resume" : "startup";
			const payload = {
				session_id: ctx.sessionManager.getSessionId(),
				transcript_path: ctx.sessionManager.getSessionFile(),
				cwd: ctx.cwd,
				hook_event_name: "SessionStart",
				source,
			};
			const parts = await Promise.all(SCRIPTS.map(script => runScript(script, payload, ctx.cwd)));
			return parts.filter(Boolean).join("\n\n");
		} catch {
			return "";
		}
	}

	pi.on("session_start", (_event, ctx) => {
		pending = gather("launch", ctx);
	});
	pi.on("session_switch", (event, ctx) => {
		pending = gather(event.reason === "new" ? "clear" : event.reason === "fork" ? "fork" : "resume", ctx);
	});
	// A new session file holding part of the conversation: AgentSession.branch, a fork from an entry, /btw.
	pi.on("session_branch", (_event, ctx) => {
		pending = gather("fork", ctx);
	});
	pi.on("session_compact", async (_event, ctx) => {
		pending = undefined;
		const content = await gather("compact", ctx);
		if (content) pi.sendMessage(customMessage(content), { deliverAs: "nextTurn" });
	});

	pi.on("before_agent_start", async event => {
		if (!pending || event.reason === "revive") return;
		offered = pending; // kept until the turn starts: omp may prepare a prompt more than once
		const content = await pending;
		return content ? { message: customMessage(content) } : undefined;
	});
	pi.on("turn_start", () => {
		if (offered && offered === pending) pending = undefined;
		offered = undefined;
	});
}
