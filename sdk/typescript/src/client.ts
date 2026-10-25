import { diffKeys, encodeContext } from "./encoding";
import type {
  ChangeListener,
  ClientPayload,
  ErrorListener,
  EvaluatedFlag,
  EventSourceLike,
  FetchLike,
  FlagClientOptions,
  FlagContext,
  FlagValue,
  ReadyListener,
} from "./types";

type Listeners = { change: ChangeListener[]; error: ErrorListener[]; ready: ReadyListener[] };

const sameFlag = (a: EvaluatedFlag, b: EvaluatedFlag) =>
  a.variation === b.variation && JSON.stringify(a.value) === JSON.stringify(b.value);

export class FlagClient {
  private readonly baseUrl: string;
  private readonly sdkKey: string;
  private readonly fetchImpl: FetchLike;
  private readonly eventSourceFactory: FlagClientOptions["eventSource"];
  private readonly streaming: boolean;
  private readonly pollIntervalMs: number;
  private readonly timeoutMs: number;
  private context: FlagContext;
  private payload: ClientPayload | null = null;
  private bootstrap: Record<string, FlagValue>;
  private etag: string | null = null;
  private source: EventSourceLike | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private listeners: Listeners = { change: [], error: [], ready: [] };
  private closed = false;

  constructor(options: FlagClientOptions) {
    if (!options.sdkKey.startsWith("cli-")) {
      throw new Error("FlagClient needs a client-side SDK key (cli-…); never ship server keys to browsers");
    }
    this.baseUrl = trimTrailingSlashes(options.baseUrl);
    this.sdkKey = options.sdkKey;
    this.context = options.context;
    this.fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
    this.eventSourceFactory = options.eventSource ?? defaultEventSource();
    this.streaming = options.streaming ?? this.eventSourceFactory !== undefined;
    this.pollIntervalMs = options.pollIntervalMs ?? 30_000;
    this.timeoutMs = options.timeoutMs ?? 5_000;
    this.bootstrap = options.bootstrap ?? {};
  }

  /** Fetch once, then start streaming (or polling). Resolves after the first successful fetch. */
  async init(): Promise<void> {
    await this.refresh();
    this.startUpdates();
  }

  /** Re-fetch the payload. Resolves to true when values changed. Never throws; errors go to on("error"). */
  async refresh(): Promise<boolean> {
    const headers: Record<string, string> = { Authorization: `Bearer ${this.sdkKey}`, Accept: "application/json" };
    if (this.etag) headers["If-None-Match"] = this.etag;
    const controller = typeof AbortController === "function" ? new AbortController() : undefined;
    const timer = controller ? setTimeout(() => controller.abort(), this.timeoutMs) : undefined;
    try {
      const init: RequestInit = { headers };
      if (controller) init.signal = controller.signal;
      const response = await this.fetchImpl(this.url("/v1/sdk/flags"), init);
      if (response.status === 304) return false;
      if (!response.ok) throw new Error(`flags request failed with HTTP ${response.status}`);
      const payload = (await response.json()) as ClientPayload;
      this.etag = response.headers.get("ETag");
      return this.install(payload);
    } catch (error) {
      this.emitError(error instanceof Error ? error : new Error(String(error)));
      return false;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /** Switch to another context (login, locale change…); re-fetches and re-subscribes. */
  async identify(context: FlagContext): Promise<void> {
    this.context = context;
    this.etag = null;
    this.stopUpdates();
    await this.refresh();
    if (!this.closed) this.startUpdates();
  }

  variation<T extends FlagValue>(key: string, defaultValue: T): T {
    const flag = this.payload?.flags[key];
    if (flag && flag.reason.kind !== "ERROR") return flag.value as T;
    if (key in this.bootstrap) return this.bootstrap[key] as T;
    return defaultValue;
  }

  isEnabled(key: string, defaultValue = false): boolean {
    return Boolean(this.variation(key, defaultValue));
  }

  detail(key: string): EvaluatedFlag | undefined {
    return this.payload?.flags[key];
  }

  allFlags(): Record<string, FlagValue> {
    const out: Record<string, FlagValue> = { ...this.bootstrap };
    for (const [key, flag] of Object.entries(this.payload?.flags ?? {})) out[key] = flag.value;
    return out;
  }

  get ready(): boolean {
    return this.payload !== null;
  }

  get version(): number | null {
    return this.payload?.version ?? null;
  }

  on(event: "change", listener: ChangeListener): () => void;
  on(event: "error", listener: ErrorListener): () => void;
  on(event: "ready", listener: ReadyListener): () => void;
  on(event: keyof Listeners, listener: ChangeListener | ErrorListener | ReadyListener): () => void {
    const list = this.listeners[event] as Array<typeof listener>;
    list.push(listener);
    return () => {
      const index = list.indexOf(listener);
      if (index >= 0) list.splice(index, 1);
    };
  }

  close(): void {
    this.closed = true;
    this.stopUpdates();
  }

  // --- internals ------------------------------------------------------------------------

  private url(path: string): string {
    return `${this.baseUrl}${path}?context=${encodeContext(this.context)}`;
  }

  private install(payload: ClientPayload): boolean {
    const first = this.payload === null;
    const changed = first ? Object.keys(payload.flags).sort() : diffKeys(this.payload!.flags, payload.flags, sameFlag);
    this.payload = payload;
    if (first) for (const l of this.listeners.ready) l(payload);
    if (changed.length > 0 && !first) for (const l of this.listeners.change) l(changed, payload);
    return changed.length > 0;
  }

  private startUpdates(): void {
    if (this.closed) return;
    if (this.streaming && this.eventSourceFactory) {
      this.source = this.eventSourceFactory(`${this.url("/v1/sdk/stream")}&sdk_key=${encodeURIComponent(this.sdkKey)}`, {
        headers: { Authorization: `Bearer ${this.sdkKey}` },
      });
      this.source.addEventListener("put", (event) => {
        try {
          this.install(JSON.parse(event.data) as ClientPayload);
        } catch (error) {
          this.emitError(error instanceof Error ? error : new Error(String(error)));
        }
      });
      this.source.addEventListener("error", () => this.emitError(new Error("flag stream disconnected")));
    } else if (this.pollIntervalMs > 0) {
      this.pollTimer = setInterval(() => void this.refresh(), this.pollIntervalMs);
    }
  }

  private stopUpdates(): void {
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private emitError(error: Error): void {
    for (const l of this.listeners.error) l(error);
  }
}

function defaultEventSource(): FlagClientOptions["eventSource"] {
  const ES = (globalThis as { EventSource?: new (url: string) => EventSourceLike }).EventSource;
  if (!ES) return undefined;
  // Browsers' EventSource cannot set headers; the service also accepts the key as a query parameter.
  return (url) => new ES(url);
}

function trimTrailingSlashes(url: string): string {
  let end = url.length;
  while (end > 0 && url.charCodeAt(end - 1) === 47 /* "/" */) end -= 1;
  return url.slice(0, end);
}
