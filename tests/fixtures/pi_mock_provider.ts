/**
 * Test-only Pi provider fixture for the V23 extension smoke test.
 *
 * It registers an offline `v23-mock` provider whose first matching turn emits
 * JSON-configured bash tool calls and every later turn emits a short text
 * reply. This lets tests exercise the real `pi` binary and the real extension
 * tool_call path without provider credentials or network access.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
	type AssistantMessage,
	type AssistantMessageEventStream,
	type Context,
	type Model,
	type SimpleStreamOptions,
	createAssistantMessageEventStream,
} from "@earendil-works/pi-ai";

function configuredCommands(): string[] {
	const raw = process.env.V23_MOCK_COMMANDS;
	if (!raw) {
		return ["pytest -q"];
	}
	try {
		const parsed = JSON.parse(raw);
		if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
			return parsed;
		}
	} catch {
		// Fall through to the default command.
	}
	return ["pytest -q"];
}

function lastUserText(context: Context): string {
	for (let index = context.messages.length - 1; index >= 0; index -= 1) {
		const message = context.messages[index];
		if (message.role !== "user") {
			continue;
		}
		if (typeof message.content === "string") {
			return message.content;
		}
		return message.content
			.map((block) => (block.type === "text" ? block.text : ""))
			.join(" ");
	}
	return "";
}

function zeroUsage(): AssistantMessage["usage"] {
	return {
		input: 0,
		output: 0,
		cacheRead: 0,
		cacheWrite: 0,
		reasoning: 0,
		totalTokens: 0,
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
	};
}

function streamMock(
	model: Model<any>,
	context: Context,
	_options?: SimpleStreamOptions,
): AssistantMessageEventStream {
	const stream = createAssistantMessageEventStream();
	const trigger = process.env.V23_MOCK_TRIGGER ?? "v23-smoke";
	const commands = configuredCommands();
	const alreadyCalled = context.messages.some(
		(message) =>
			message.role === "assistant" &&
			Array.isArray(message.content) &&
			message.content.some((block) => block.type === "toolCall"),
	);
	const emitToolCalls = !alreadyCalled && lastUserText(context).includes(trigger);

	(async () => {
		const output: AssistantMessage = {
			role: "assistant",
			content: [],
			api: model.api,
			provider: model.provider,
			model: model.id,
			usage: zeroUsage(),
			stopReason: "pending",
			timestamp: Date.now(),
		};
		try {
			stream.push({ type: "start", partial: output });
			if (emitToolCalls) {
				commands.forEach((command, index) => {
					const toolCall = {
						type: "toolCall" as const,
						id: `v23-mock-call-${index}`,
						name: "bash",
						arguments: { command },
					};
					output.content.push(toolCall);
					stream.push({ type: "toolcall_start", contentIndex: index, partial: output });
					stream.push({
						type: "toolcall_end",
						contentIndex: index,
						toolCall,
						partial: output,
					});
				});
				output.stopReason = "toolUse";
			} else {
				output.content.push({ type: "text", text: "v23-mock-done" });
				stream.push({ type: "text_start", contentIndex: 0, partial: output });
				stream.push({
					type: "text_end",
					contentIndex: 0,
					content: "v23-mock-done",
					partial: output,
				});
				output.stopReason = "stop";
			}
			stream.push({ type: "done", reason: output.stopReason, message: output });
			stream.end();
		} catch (error) {
			output.stopReason = "error";
			output.errorMessage = error instanceof Error ? error.message : String(error);
			stream.push({ type: "error", reason: "error", error: output });
			stream.end();
		}
	})();

	return stream;
}

export default function (pi: ExtensionAPI) {
	pi.registerProvider("v23-mock", {
		name: "V23 mock provider",
		baseUrl: "http://127.0.0.1:9",
		apiKey: "v23-mock-key",
		api: "openai-completions",
		models: [
			{
				id: "v23-mock-1",
				name: "V23 mock model",
				reasoning: false,
				input: ["text"],
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
				contextWindow: 32768,
				maxTokens: 2048,
			},
		],
		streamSimple: streamMock,
	});
}