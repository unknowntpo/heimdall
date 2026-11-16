// Spec 0005 B10-B15, B19, B20: pure decisions for the Heimdall pi extension. No pi imports, so `node --test` can run it.
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
/** `cancelled`: the caller's signal (Esc) aborted the check (B20). That is not a service failure. */
export type Outcome = { ok: true; verdict: Verdict } | { ok: false; error: string; cancelled?: boolean };
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
const partly = (v: Verdict) => `only the first ${v.n_windows} windows were checked, the rest of the text was not (${fmt(v)})`;
const isCancelled = (o: Outcome) => !o.ok && o.cancelled === true;

export function decideInput(mode: Mode, outcome: Outcome, hasUI: boolean): InputAction {
	if (mode === "off") return { kind: "pass" };
	if (isCancelled(outcome)) // B20
		return mode === "block" ? { kind: "handle", message: "Heimdall check was cancelled. Message not sent (mode block)." } : { kind: "pass" };
	if (!outcome.ok) return { kind: "pass" }; // B15 fail open
	const v = outcome.verdict;
	if (v.label === "injection") {
		const what = `Heimdall flagged your message as prompt injection (${fmt(v)}).`;
		if (mode === "warn") return { kind: "warn", message: `${what} Sent anyway (mode warn).` };
		if (hasUI) return { kind: "confirm", title: "Heimdall: possible prompt injection", message: `${what} Send it anyway?` };
		return { kind: "handle", message: `${what} Not sent (mode block).` };
	}
	if (!v.truncated) return { kind: "pass" };
	const what = `Heimdall: your message is too long to check completely; ${partly(v)}.`; // B19
	if (mode === "warn") return { kind: "warn", message: `${what} Sent anyway (mode warn).` };
	if (hasUI) return { kind: "confirm", title: "Heimdall: message only partly checked", message: `${what} Send it anyway?` };
	return { kind: "handle", message: `${what} Not sent (mode block).` };
}

export function decideTool(mode: Mode, toolName: string, outcome: Outcome): ToolAction {
	if (mode === "off") return { kind: "pass" };
	if (isCancelled(outcome)) // B20: warn mode passes silently
		return mode === "block"
			? {
					kind: "replace",
					text: `[Heimdall] The output of tool "${toolName}" was withheld: the Heimdall check was cancelled.`,
					message: `Heimdall check was cancelled; the output of tool "${toolName}" was withheld.`,
				}
			: { kind: "pass" };
	if (!outcome.ok) return { kind: "pass" }; // B15 fail open
	const v = outcome.verdict;
	if (v.label === "injection") {
		if (mode === "warn")
			return { kind: "warn", message: `Heimdall flagged the output of tool "${toolName}" as prompt injection (${fmt(v)}). Passed through (mode warn).` };
		return {
			kind: "replace",
			text: `[Heimdall] The output of tool "${toolName}" was withheld: it was classified as prompt injection (${fmt(v)}). Tell the user that this tool output was blocked.`,
			message: `Heimdall blocked the output of tool "${toolName}" (${fmt(v)}).`,
		};
	}
	if (!v.truncated) return { kind: "pass" };
	if (mode === "warn") // B19
		return { kind: "warn", message: `Heimdall: the output of tool "${toolName}" is too long to check completely; ${partly(v)}. Passed through (mode warn).` };
	return {
		kind: "replace",
		text: `[Heimdall] The output of tool "${toolName}" was withheld: it is too long to check completely; ${partly(v)}. Tell the user that this tool output was withheld because Heimdall could not check all of it.`,
		message: `Heimdall withheld the output of tool "${toolName}": ${partly(v)}.`,
	};
}

export function statusText(mode: Mode, outcome?: Outcome): string {
	if (mode === "off") return "heimdall: off";
	if (!outcome) return `heimdall: ${mode}`;
	if (isCancelled(outcome)) return "heimdall: cancelled";
	if (!outcome.ok) return "heimdall: unavailable (fail open)";
	const v = outcome.verdict;
	return `heimdall: ${v.label} ${v.score.toFixed(3)} (${Math.round(v.latency_ms)} ms${v.truncated ? ", truncated" : ""})`;
}

/** B15: warn only on the first failure of a streak; a success ends the streak. B20: a cancel changes nothing. */
export function nextFailure(failing: boolean, outcome: Outcome): { failing: boolean; warn: boolean } {
	if (isCancelled(outcome)) return { failing, warn: false };
	if (outcome.ok) return { failing: false, warn: false };
	return { failing: true, warn: !failing };
}

/** One request with a timeout and an optional caller signal; never throws. */
export async function callService(
	url: string,
	path: string,
	init: RequestInit,
	opts: CallOptions = {},
): Promise<{ ok: true; body: unknown } | { ok: false; error: string; cancelled?: boolean }> {
	const ms = opts.timeoutMs ?? TIMEOUT_MS;
	const cancelled = { ok: false as const, error: `cancelled (${url}${path})`, cancelled: true };
	if (opts.signal?.aborted) return cancelled; // B20: Esc before the check started; send nothing
	const timer = new AbortController();
	const handle = setTimeout(() => timer.abort(new DOMException("timeout", "TimeoutError")), ms);
	const signal = opts.signal ? AbortSignal.any([opts.signal, timer.signal]) : timer.signal;
	try {
		const res = await (opts.fetchImpl ?? fetch)(url + path, { ...init, signal });
		if (res.status !== 200) return { ok: false, error: `HTTP ${res.status} from ${url}${path}` };
		return { ok: true, body: await res.json() };
	} catch (e) {
		if (opts.signal?.aborted) return cancelled; // B20: decided by the caller's signal, not the error name
		const err = e as Error;
		if (err?.name === "TimeoutError") return { ok: false, error: `timeout after ${ms} ms (${url}${path})` };
		return { ok: false, error: `${err?.name ?? "Error"}: ${err?.message ?? String(e)} (${url}${path})` };
	} finally {
		clearTimeout(handle);
	}
}

/** B15: all seven B4 fields with the right types. */
function isVerdict(b: any): b is Verdict {
	return !!b && (b.label === "safe" || b.label === "injection") && typeof b.score === "number" &&
		typeof b.model_version === "string" && typeof b.threshold === "number" && typeof b.latency_ms === "number" &&
		typeof b.n_windows === "number" && typeof b.truncated === "boolean";
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
