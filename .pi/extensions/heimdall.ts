/**
 * Heimdall guard for pi (Spec 0005 B10-B15).
 *
 * Sends each user message (`input`) and each tool output (`tool_result`) to a local `heimdall serve`
 * and blocks or warns on prompt injection. Fails open when the service is unavailable (demo harness).
 *
 * 1. uv run heimdall serve
 * 2. pi            (in this repo; project-local extensions are discovered from .pi/extensions/)
 * 3. /heimdall     (mode and service health; /heimdall block|warn|off)
 */

import type { ExtensionAPI, ExtensionContext } from "@mariozechner/pi-coding-agent";
import {
	type Mode,
	type Outcome,
	callService,
	classify,
	decideInput,
	decideTool,
	joinText,
	nextFailure,
	parseCommand,
	serviceUrl,
	shouldClassifyInput,
	shouldClassifyTool,
	statusText,
} from "./heimdall/decide.ts";

type Deps = { fetchImpl: (url: string, init: RequestInit) => Promise<Response>; env: Record<string, string | undefined> };

export function setup(pi: ExtensionAPI, deps: Deps): void {
	const url = serviceUrl(deps.env);
	let mode: Mode = "block";
	let failing = false;

	async function check(text: string, ctx: ExtensionContext): Promise<Outcome> {
		const outcome = await classify(url, text, { signal: ctx.signal, fetchImpl: deps.fetchImpl });
		const next = nextFailure(failing, outcome);
		failing = next.failing;
		if (!outcome.ok && next.warn)
			ctx.ui.notify(`Heimdall service unavailable, passing content through (fail open): ${outcome.error}`, "warning");
		ctx.ui.setStatus("heimdall", statusText(mode, outcome));
		return outcome;
	}

	pi.on("session_start", async (_event, ctx) => {
		ctx.ui.setStatus("heimdall", statusText(mode));
	});

	pi.on("input", async (event, ctx) => {
		if (!shouldClassifyInput(mode, event.source, event.text)) return { action: "continue" };
		const action = decideInput(mode, await check(event.text, ctx), ctx.hasUI);
		switch (action.kind) {
			case "pass":
				return { action: "continue" };
			case "warn":
				ctx.ui.notify(action.message, "warning");
				return { action: "continue" };
			case "handle":
				ctx.ui.notify(action.message, "warning");
				return { action: "handled" };
			case "confirm":
				if (await ctx.ui.confirm(action.title, action.message)) return { action: "continue" };
				ctx.ui.notify("Message not sent.", "info");
				return { action: "handled" };
		}
	});

	pi.on("tool_result", async (event, ctx) => {
		const text = joinText(event.content);
		if (!shouldClassifyTool(mode, text)) return undefined;
		const action = decideTool(mode, event.toolName, await check(text, ctx));
		if (action.kind === "pass") return undefined;
		ctx.ui.notify(action.message, "warning");
		if (action.kind === "warn") return undefined;
		return { content: [{ type: "text", text: action.text }] };
	});

	pi.registerCommand("heimdall", {
		description: "Heimdall guard: show mode and service health, or set mode block|warn|off",
		getArgumentCompletions: (prefix: string) =>
			["block", "warn", "off"].filter((m) => m.startsWith(prefix)).map((m) => ({ value: m, label: m })),
		handler: async (args, ctx) => {
			const cmd = parseCommand(args);
			if (cmd.kind === "usage") return ctx.ui.notify(cmd.message, "error");
			if (cmd.kind === "set") {
				mode = cmd.mode;
				ctx.ui.setStatus("heimdall", statusText(mode));
				return ctx.ui.notify(`Heimdall mode: ${mode}`, "info");
			}
			const h = await callService(url, "/health", {}, { signal: ctx.signal, fetchImpl: deps.fetchImpl });
			const b = h.ok ? (h.body as { model_version?: string; threshold?: number }) : undefined;
			const health = b ? `service ok at ${url}, model ${b.model_version}, threshold ${b.threshold}` : `service unavailable: ${!h.ok ? h.error : ""}`;
			ctx.ui.notify(`Heimdall mode: ${mode}; ${health}`, h.ok ? "info" : "warning");
		},
	});
}

export default function heimdall(pi: ExtensionAPI): void {
	setup(pi, { fetchImpl: (u, i) => fetch(u, i), env: process.env });
}
