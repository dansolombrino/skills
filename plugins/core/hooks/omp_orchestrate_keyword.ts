/**
 * The word "orchestrate" in a prompt, for omp: route it to this marketplace's multi-agent skills.
 *
 * omp's built-in `orchestrate` magic keyword attaches a fixed contract that only knows omp's own
 * subagents, and no setting changes that contract. With it off (`magicKeywords.orchestrate: false`)
 * this extension takes the word over: a prompt that uses it gets one hidden note telling the model
 * which skill to load, or to use its own subagents when neither fits. With the built-in still on,
 * both notes arrive; the routing note only adds to it.
 *
 * Matching follows the built-in's rules so source code and paths do not trigger it: exact
 * lowercase, standalone prose (punctuation and quotes may touch it, but not letters, digits, `_`,
 * `/`, `\`, `-`, a file extension, `::`, `.name` or call syntax), and nothing inside fenced code,
 * inline code, HTML comments, tags or elements. Subagent prompts and subagent revivals get
 * nothing, as with the built-in, which skips agent-initiated prompts.
 */
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const MESSAGE_TYPE = "core-orchestrate-keyword";

const NOTE = `<system-reminder>
The user wrote "orchestrate": they want this request run as coordinated multi-agent work. Choose the mechanism from the request, and load its skill before starting:
- Several separate work items (tasks, findings, fixes, features), each handled by its own main agent on its own branch and worktree and reporting back to this chat: the \`agent-fleet\` skill. It brings in its Orca adapter, \`agent-fleet-orca\`, when it runs inside Orca.
- Work that needs Orca workers: a different agent (such as Codex), its own Orca workspace, or supervision (task DAG, ask/reply, decision gates): the \`orchestration\` skill.
- Otherwise, a single task: scope all of it, delegate substantial independent parts to your own parallel subagents, verify each phase, and continue until the request is complete.
If the chosen skill is not available in this session, say so and use your own subagents.
</system-reminder>`;

/** Text that is not prose: fenced code, inline code, HTML comments, elements with their content, lone tags. */
const NON_PROSE = [
	/^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n {0,3}\1[`~]*[ \t]*(?=\n|$)|$)/gm,
	/(`+)[\s\S]*?\1/g,
	/<!--[\s\S]*?(?:-->|$)/g,
	/<([A-Za-z][\w:-]*)\b[^>]*>[\s\S]*?<\/\1\s*>/g,
	/<\/?[A-Za-z][\w:-]*\b[^>]*>/g,
];

const KEYWORD = /(?<![\w/\\-])(?<!\w\.)(?<!::)orchestrate(?![\w/\\(-]|\.\w|::)/;

/** Whether `prompt` uses the keyword as prose. */
function mentionsOrchestrate(prompt: string): boolean {
	let prose = prompt;
	for (const pattern of NON_PROSE) prose = prose.replace(pattern, " ");
	return KEYWORD.test(prose);
}

export default function orchestrateKeyword(pi: ExtensionAPI) {
	pi.on("before_agent_start", (event, ctx) => {
		if (event.reason === "revive" || ctx.agent?.kind === "sub") return;
		if (!mentionsOrchestrate(event.prompt ?? "")) return;
		return { message: { customType: MESSAGE_TYPE, content: NOTE, display: false, attribution: "user" as const } };
	});
}
