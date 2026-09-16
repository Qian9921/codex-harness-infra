/**
 * V23 owned Pi extension: finite RTK routing for the LLM `bash` tool.
 *
 * Loaded explicitly by the V23 bridge together with `--no-extensions`, so no
 * user extension discovery changes. Bare `pytest` commands are rewritten to
 * the verified `rtk pytest` route before the built-in bash tool executes them.
 * `python -m pytest`, `pytest3`, and explicit interpreter or pytest paths stay
 * raw because that route does not prove it preserves the selected executable.
 * Multiline commands stay raw, unsupported compound shell syntax is left
 * untouched, exact JSON/porcelain/diff/diagnostic flags stay raw, and a
 * missing or unusable configured RTK executable falls back to the raw command
 * with an explicit note in the tool result and in the routing log. Exit status
 * and diagnostics are the built-in bash tool's own result; this extension
 * never reruns a command.
 */

import { appendFileSync, statSync } from "node:fs";
import { delimiter, join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";

const RTK_FLAG = "v23-rtk-log";

/** Exact-format or raw-diagnostic arguments never routed through the summarizer. */
const RAW_EXCEPTION_ARGS = new Set([
	"--json",
	"--json-report",
	"--junitxml",
	"--xml",
	"--porcelain",
	"--tb=long",
	"--tb=native",
	"-vv",
	"-vvv",
	"-s",
	"--capture=no",
	"--pdb",
	"--collect-only",
	"--co",
	"--fixtures",
	"--markers",
	"--help",
	"--version",
	"-h",
]);

/** Options whose separate-value and `=`-joined forms both stay raw. */
const RAW_EXCEPTION_OPTIONS = new Set([
	"--junitxml",
	"--junit-xml",
	"--xml",
	"--json-report",
	"--json-report-file",
	"--tb",
	"--capture",
	"--log-file",
	"--result-log",
]);

const PYTHON_RE = /^python(?:\d+(?:\.\d+)*)?$/;
const PATHY_RE = /[/\\]/;

interface RouteDecision {
	route: string;
	args: string[];
}

interface PendingCall {
	kind: "routed" | "raw-exception" | "fallback";
	original: string;
	command?: string;
	reason?: string;
}

type JsonRecord = Record<string, unknown>;

function isExecutableFile(path: string): boolean {
	try {
		const info = statSync(path);
		return info.isFile() && (info.mode & 0o111) !== 0;
	} catch {
		return false;
	}
}

interface RtkResolution {
	executable: string | null;
	reason: string;
}

function searchPath(name: string): string | null {
	for (const directory of (process.env.PATH ?? "").split(delimiter)) {
		if (!directory) {
			continue;
		}
		const candidate = join(directory, name);
		if (isExecutableFile(candidate)) {
			return candidate;
		}
	}
	return null;
}

/**
 * Resolve rtk from the configured override or PATH without scanning trees.
 * A configured `V23_RTK_BIN` is authoritative: when it is set but unusable,
 * routing declines with an explicit reason instead of silently using PATH.
 */
function resolveRtk(): RtkResolution {
	const explicit = (process.env.V23_RTK_BIN ?? "").trim();
	if (explicit) {
		if (isExecutableFile(explicit)) {
			return { executable: explicit, reason: "" };
		}
		if (!PATHY_RE.test(explicit)) {
			const found = searchPath(explicit);
			if (found) {
				return { executable: found, reason: "" };
			}
		}
		return { executable: null, reason: `configured-rtk-unavailable: ${explicit}` };
	}
	const found = searchPath("rtk");
	if (found) {
		return { executable: found, reason: "" };
	}
	return { executable: null, reason: "rtk-not-found" };
}

/**
 * Tokenize one simple command. Returns null for anything the parser cannot
 * prove literal: shell operators, expansions, globs, redirections, comments,
 * substitutions, or an env-assignment prefix. A null result stays raw.
 */
function parseSimpleCommand(command: string): string[] | null {
	if (command.includes("\n") || command.includes("\r")) {
		return null;
	}
	const tokens: string[] = [];
	let current = "";
	let started = false;
	let index = 0;
	while (index < command.length) {
		const char = command[index];
		if (char === "'") {
			started = true;
			index += 1;
			const close = command.indexOf("'", index);
			if (close === -1) {
				return null;
			}
			current += command.slice(index, close);
			index = close + 1;
			continue;
		}
		if (char === '"') {
			started = true;
			index += 1;
			let closed = false;
			while (index < command.length) {
				const inner = command[index];
				if (inner === '"') {
					closed = true;
					index += 1;
					break;
				}
				if (inner === "$" || inner === "`") {
					return null;
				}
				if (inner === "\\") {
					const escaped = command[index + 1];
					if (escaped === undefined) {
						return null;
					}
					if (escaped === "$" || escaped === "`" || escaped === '"' || escaped === "\\") {
						current += escaped;
						index += 2;
					} else {
						// POSIX double quotes keep a backslash before any other
						// character literal; stripping it would change argv.
						current += "\\";
						index += 1;
					}
					continue;
				}
				current += inner;
				index += 1;
			}
			if (!closed) {
				return null;
			}
			continue;
		}
		if (char === "\\") {
			if (index + 1 >= command.length) {
				return null;
			}
			started = true;
			current += command[index + 1];
			index += 2;
			continue;
		}
		if (char === " " || char === "\t") {
			if (started) {
				tokens.push(current);
				current = "";
				started = false;
			}
			index += 1;
			continue;
		}
		if ("|&;<>()#*?[]{}!`$~".includes(char)) {
			return null;
		}
		started = true;
		current += char;
		index += 1;
	}
	if (started) {
		tokens.push(current);
	}
	if (tokens.length === 0) {
		return null;
	}
	if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[0])) {
		return null;
	}
	return tokens;
}

function basename(value: string): string {
	const normalized = value.replace(/\\/g, "/");
	const parts = normalized.split("/");
	return parts[parts.length - 1] ?? value;
}

/**
 * Map a simple argv to the finite verified RTK pytest route, or null for raw.
 * Only a bare literal `pytest` is proven to preserve the selected executable:
 * `rtk pytest` has no interpreter argument, so any explicit interpreter or
 * pytest path stays raw instead of silently switching environments.
 */
function routePytest(argv: string[]): RouteDecision | null {
	if (argv[0] === "pytest") {
		return { route: "pytest", args: argv.slice(1) };
	}
	return null;
}

/** Explain an explicit interpreter/pytest selection that stays raw. */
function rawPytestReason(argv: string[]): string | null {
	const head = argv[0] ?? "";
	const name = basename(head);
	const modulePytest = argv[1] === "-m" && argv[2] === "pytest";
	if (PATHY_RE.test(head) && (name === "pytest" || name === "pytest3")) {
		return "explicit-pytest-path-stays-raw";
	}
	if (PATHY_RE.test(head) && PYTHON_RE.test(name) && modulePytest) {
		return "explicit-interpreter-path-stays-raw";
	}
	if (name === "pytest3" && !PATHY_RE.test(head)) {
		return "pytest3-stays-raw";
	}
	if (PYTHON_RE.test(head) && modulePytest) {
		return "python-m-pytest-may-select-a-different-interpreter";
	}
	return null;
}

function hasRawException(args: string[]): boolean {
	return args.some((arg) => {
		if (RAW_EXCEPTION_ARGS.has(arg)) {
			return true;
		}
		for (const option of RAW_EXCEPTION_OPTIONS) {
			if (arg === option || arg.startsWith(`${option}=`)) {
				return true;
			}
		}
		return false;
	});
}

function shellQuote(arg: string): string {
	if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(arg)) {
		return arg;
	}
	return `'${arg.replaceAll("'", "'\\''")}'`;
}

function renderCommand(argv: string[]): string {
	return argv.map(shellQuote).join(" ");
}

function logPath(pi: ExtensionAPI): string | null {
	const flag = pi.getFlag(RTK_FLAG);
	if (typeof flag === "string" && flag.trim()) {
		return flag;
	}
	const env = process.env.V23_RTK_LOG;
	return env && env.trim() ? env : null;
}

function appendLog(pi: ExtensionAPI, record: JsonRecord): void {
	const target = logPath(pi);
	if (!target) {
		return;
	}
	try {
		appendFileSync(
			target,
			`${JSON.stringify({ timestamp: new Date().toISOString(), ...record })}\n`,
		);
	} catch {
		// Routing must never fail because the receipt log is unavailable.
	}
}

export default function (pi: ExtensionAPI) {
	pi.registerFlag(RTK_FLAG, {
		type: "string",
		description: "Write V23 RTK routing receipts to this JSONL path.",
	});
	const pending = new Map<string, PendingCall>();

	pi.on("tool_call", async (event) => {
		if (!isToolCallEventType("bash", event)) {
			return;
		}
		const command = event.input.command;
		if (typeof command !== "string" || !command.trim()) {
			return;
		}
		if (command.includes("\n") || command.includes("\r")) {
			// A multiline command has shell semantics the single-command parser
			// cannot preserve; re-parsing it as one command would merge or drop
			// separators, so it always stays raw.
			appendLog(pi, {
				event: "rtk-raw-exception",
				toolCallId: event.toolCallId,
				command,
				reason: "multiline-command-stays-raw",
			});
			return;
		}
		const argv = parseSimpleCommand(command);
		if (!argv) {
			return;
		}
		const decision = routePytest(argv);
		if (!decision) {
			const reason = rawPytestReason(argv);
			if (reason) {
				appendLog(pi, {
					event: "rtk-raw-exception",
					toolCallId: event.toolCallId,
					command,
					reason,
				});
			}
			return;
		}
		if (hasRawException(decision.args)) {
			pending.set(event.toolCallId, { kind: "raw-exception", original: command });
			appendLog(pi, {
				event: "rtk-raw-exception",
				toolCallId: event.toolCallId,
				command,
				reason: "exact-format-or-diagnostic-flags-stay-raw",
			});
			return;
		}
		const rtk = resolveRtk();
		if (!rtk.executable) {
			pending.set(event.toolCallId, {
				kind: "fallback",
				original: command,
				reason: rtk.reason,
			});
			appendLog(pi, {
				event: "rtk-fallback",
				toolCallId: event.toolCallId,
				command,
				reason: `${rtk.reason}; raw command executed`,
			});
			return;
		}
		const rewritten = renderCommand([rtk.executable, decision.route, ...decision.args]);
		event.input.command = rewritten;
		pending.set(event.toolCallId, {
			kind: "routed",
			original: command,
			command: rewritten,
		});
		appendLog(pi, {
			event: "rtk-route",
			toolCallId: event.toolCallId,
			route: decision.route,
			original: command,
			command: rewritten,
		});
	});

	pi.on("tool_result", async (event) => {
		const entry = pending.get(event.toolCallId);
		if (!entry) {
			return;
		}
		pending.delete(event.toolCallId);
		appendLog(pi, {
			event: "rtk-result",
			toolCallId: event.toolCallId,
			kind: entry.kind,
			original: entry.original,
			command: entry.command,
			isError: event.isError,
			reason: entry.reason,
		});
		if (entry.kind === "fallback") {
			return {
				content: [
					...event.content,
					{
						type: "text" as const,
						text: `[v23-routing] ${entry.reason}: rtk unavailable; the raw command ran unchanged.`,
					},
				],
			};
		}
		return undefined;
	});
}