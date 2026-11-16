// Spec 0005 B10-B15: pure decisions for the Heimdall pi extension. No pi imports, so `node --test` can run it.
// This directory has no index.ts and no package.json, so pi does not load it as an extension.

export const DEFAULT_URL = "http://127.0.0.1:8765";
export const TIMEOUT_MS = 5000;

export type Mode = "block" | "warn" | "off";
export type Verdict = {
	label: "safe" | "injection";
	score: number;
	model_version: string;
	threshold: number;
	latency_ms: number;
	n_windows: number;
	truncated: boolean;
};
export type Outcome = { ok: true; verdict: Verdict } | { ok: false; error: string };
export type InputAction =
	| { kind: "pass" }
	| { kind: "warn"; message: string }
	| { kind: "confirm"; title: string; message: string }
	| { kind: "handle"; message: string };
export type ToolAction = { kind: "pass" } | { kind: "warn"; message: string } | { kind: "replace"; text: string; message: string };
export type Command = { kind: "set"; mode: Mode } | { kind: "show" } | { kind: "usage"; message: string };
type Part = { type: string; text?: string };
type FetchLike = (url: string, init: RequestInit) => Promise<Response>;
export type CallOptions = { signal?: AbortSignal; timeoutMs?: number; fetchImpl?: FetchLike };

export function serviceUrl(env: Record<string, string | undefined>): string {
	return (env.HEIMDALL_URL || DEFAULT_URL).replace(/\/+$/, "");
}

export function parseCommand(args: string): Command {
	const a = args.trim();
	if (a === "") return { kind: "show" };
	if (a === "block" || a === "warn" || a === "off") return { kind: "set", mode: a };
	return { kind: "usage", message: `unknown argument "${a}"; usage: /heimdall [block|warn|off]` };
}

export function joinText(content: Part[]): string {
	return content.filter((p) => p.type === "text" && typeof p.text === "string").map((p) => p.text).join("\n");
}

export function shouldClassifyInput(mode: Mode, source: string, text: string): boolean {
	return mode !== "off" && source !== "extension" && text.trim() !== "";
}

export function shouldClassifyTool(mode: Mode, text: string): boolean {
	return mode !== "off" && text.trim() !== "";
}

const fmt = (v: Verdict) => `score ${v.score.toFixed(3)}, threshold ${v.threshold.toFixed(3)}, model ${v.model_version}`;

export function decideInput(mode: Mode, outcome: Outcome, hasUI: boolean): InputAction {
	if (!outcome.ok || outcome.verdict.label !== "injection" || mode === "off") return { kind: "pass" };
	const what = `Heimdall flagged your message as prompt injection (${fmt(outcome.verdict)}).`;
	if (mode === "warn") return { kind: "warn", message: `${what} Sent anyway (mode warn).` };
	if (hasUI) return { kind: "confirm", title: "Heimdall: possible prompt injection", message: `${what} Send it anyway?` };
	return { kind: "handle", message: `${what} Not sent (mode block).` };
}

export function decideTool(mode: Mode, toolName: string, outcome: Outcome): ToolAction {
	if (!outcome.ok || outcome.verdict.label !== "injection" || mode === "off") return { kind: "pass" };
	const v = outcome.verdict;
	if (mode === "warn")
		return { kind: "warn", message: `Heimdall flagged the output of tool "${toolName}" as prompt injection (${fmt(v)}). Passed through (mode warn).` };
	return {
		kind: "replace",
		text: `[Heimdall] The output of tool "${toolName}" was withheld: it was classified as prompt injection (${fmt(v)}). Tell the user that this tool output was blocked.`,
		message: `Heimdall blocked the output of tool "${toolName}" (${fmt(v)}).`,
	};
}

export function statusText(mode: Mode, outcome?: Outcome): string {
	if (mode === "off") return "heimdall: off";
	if (!outcome) return `heimdall: ${mode}`;
	if (!outcome.ok) return "heimdall: unavailable (fail open)";
	const v = outcome.verdict;
	return `heimdall: ${v.label} ${v.score.toFixed(3)} (${Math.round(v.latency_ms)} ms)`;
}

/** B15: warn only on the first failure of a streak; a success ends the streak. */
export function nextFailure(failing: boolean, outcome: Outcome): { failing: boolean; warn: boolean } {
	if (outcome.ok) return { failing: false, warn: false };
	return { failing: true, warn: !failing };
}

/** One request with a timeout and an optional caller signal; never throws. */
export async function callService(
	url: string,
	path: string,
	init: RequestInit,
	opts: CallOptions = {},
): Promise<{ ok: true; body: unknown } | { ok: false; error: string }> {
	const ms = opts.timeoutMs ?? TIMEOUT_MS;
	const timer = new AbortController();
	const handle = setTimeout(() => timer.abort(new DOMException("timeout", "TimeoutError")), ms);
	const signal = opts.signal ? AbortSignal.any([opts.signal, timer.signal]) : timer.signal;
	try {
		const res = await (opts.fetchImpl ?? fetch)(url + path, { ...init, signal });
		if (res.status !== 200) return { ok: false, error: `HTTP ${res.status} from ${url}${path}` };
		return { ok: true, body: await res.json() };
	} catch (e) {
		const err = e as Error;
		if (err?.name === "TimeoutError") return { ok: false, error: `timeout after ${ms} ms (${url}${path})` };
		return { ok: false, error: `${err?.name ?? "Error"}: ${err?.message ?? String(e)} (${url}${path})` };
	} finally {
		clearTimeout(handle);
	}
}

function isVerdict(b: any): b is Verdict {
	return b && (b.label === "safe" || b.label === "injection") && typeof b.score === "number" &&
		typeof b.model_version === "string" && typeof b.threshold === "number" && typeof b.latency_ms === "number";
}

export async function classify(url: string, text: string, opts: CallOptions = {}): Promise<Outcome> {
	const r = await callService(url, "/classify", {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({ text }),
	}, opts);
	if (!r.ok) return r;
	if (!isVerdict(r.body)) return { ok: false, error: "unexpected response from /classify" };
	return { ok: true, verdict: r.body };
}
