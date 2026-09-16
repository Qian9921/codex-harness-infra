/**
 * V23 owned Pi extension: finite RTK routing for the LLM `bash` tool.
 *
 * Loaded explicitly by the V23 bridge together with `--no-extensions`, so no
 * user extension discovery changes. Plain `pytest` and `python -m pytest`
 * commands are rewritten to the verified `rtk pytest` route before the
 * built-in bash tool executes them. Unsupported compound shell syntax is left
 * untouched, exact JSON/porcelain/diff/diagnostic flags stay raw, and a
 * missing RTK executable falls back to the raw command with an explicit note
 * in the tool result and in the routing log. Exit status and diagnostics are
 * the built-in bash tool's own result; this extension never reruns a command.
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
]);

const PYTHON_RE = /^python(?:3(?:\.\d+)?)?$/;

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

/** Resolve rtk from the configured override or PATH without scanning trees. */
function resolveRtk(): string | null {
	const explicit = process.env.V23_RTK_BIN;
	if (explicit && isExecutableFile(explicit)) {
		return explicit;
	}
	for (const directory of (process.env.PATH ?? "").split(delimiter)) {
		if (!directory) {
			continue;
		}
		const candidate = join(directory, "rtk");
		if (isExecutableFile(candidate)) {
			return candidate;
		}
	}
	return null;
}

/**
 * Tokenize one simple command. Returns null for anything the parser cannot
 * prove literal: shell operators, expansions, globs, redirections, comments,
 * substitutions, or an env-assignment prefix. A null result stays raw.
 */
function parseSimpleCommand(command: string): string[] | null {
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
					if (index + 1 >= command.length) {
						return null;
					}
					current += command[index + 1];
					index += 2;
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
		if (/\s/.test(char)) {
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

/** Map a simple argv to the finite verified RTK pytest route, or null for raw. */
function routePytest(argv: string[]): RouteDecision | null {
	const head = basename(argv[0]);
	if (head === "pytest" || head === "pytest3") {
		return { route: "pytest", args: argv.slice(1) };
	}
	if (PYTHON_RE.test(head) && argv[1] === "-m" && argv[2] === "pytest") {
		return { route: "pytest", args: argv.slice(3) };
	}
	return null;
}

function hasRawException(args: string[]): boolean {
	return args.some((arg) => RAW_EXCEPTION_ARGS.has(arg));
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
		const argv = parseSimpleCommand(command);
		if (!argv) {
			return;
		}
		const decision = routePytest(argv);
		if (!decision) {
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
		if (!rtk) {
			pending.set(event.toolCallId, {
				kind: "fallback",
				original: command,
				reason: "rtk-not-found",
			});
			appendLog(pi, {
				event: "rtk-fallback",
				toolCallId: event.toolCallId,
				command,
				reason: "rtk-not-found; raw command executed",
			});
			return;
		}
		const rewritten = renderCommand([rtk, decision.route, ...decision.args]);
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