export type FlagValue = boolean | string | number | Record<string, unknown> | unknown[] | null;

export interface EvaluationReason {
  kind: "OFF" | "TARGET_MATCH" | "RULE_MATCH" | "FALLTHROUGH" | "ERROR";
  rule_id?: string;
  rule_index?: number;
  in_rollout?: boolean;
  error?: string;
}

/** One evaluated flag as returned to client-side SDK keys. */
export interface EvaluatedFlag {
  value: FlagValue;
  variation: string | null;
  reason: EvaluationReason;
  version: number;
}

export interface ClientPayload {
  environment: string;
  version: number;
  context_key: string;
  flags: Record<string, EvaluatedFlag>;
}

export interface FlagContext {
  key: string;
  attributes?: Record<string, unknown>;
}

export type ChangeListener = (changedKeys: string[], payload: ClientPayload) => void;
export type ErrorListener = (error: Error) => void;
export type ReadyListener = (payload: ClientPayload) => void;

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** Just the part of EventSource the client uses, so it can be injected in tests and Node. */
export interface EventSourceLike {
  addEventListener(type: string, listener: (event: { data: string }) => void): void;
  close(): void;
}
export type EventSourceFactory = (url: string, options?: { headers?: Record<string, string> }) => EventSourceLike;

export interface FlagClientOptions {
  /** Base URL of the service, e.g. https://flags.example.com */
  baseUrl: string;
  /** A client-side SDK key (cli-…). Server keys must never ship to browsers. */
  sdkKey: string;
  /** Who is being evaluated. Change it later with identify(). */
  context: FlagContext;
  /** Open an SSE connection for live updates after init(). Default: true when EventSource exists. */
  streaming?: boolean;
  /** Milliseconds between polls when not streaming. 0 disables polling. Default 30000. */
  pollIntervalMs?: number;
  /** Known values to serve before the first fetch completes. */
  bootstrap?: Record<string, FlagValue>;
  /** Injected for tests / non-browser runtimes. */
  fetch?: FetchLike;
  eventSource?: EventSourceFactory;
  /** Request timeout in milliseconds. Default 5000. */
  timeoutMs?: number;
}
