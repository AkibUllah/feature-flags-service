import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FlagClient, encodeContext } from "../src/index";
import type { ClientPayload, EventSourceLike } from "../src/types";

const payload = (version: number, checkout: boolean, banner = "Eid Mubarak"): ClientPayload => ({
  environment: "prod",
  version,
  context_key: "u1",
  flags: {
    "new-checkout": { value: checkout, variation: checkout ? "on" : "off", reason: { kind: "FALLTHROUGH" }, version },
    banner: { value: banner, variation: "eid", reason: { kind: "RULE_MATCH", rule_id: "bd", rule_index: 0 }, version: 1 },
  },
});

class FakeServer {
  payload = payload(1, true);
  etag = '"v1"';
  requests: { url: string; headers: Record<string, string> }[] = [];
  status: number | null = null;
  fail = false;

  fetch = async (input: string, init?: RequestInit): Promise<Response> => {
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>));
    this.requests.push({ url: input, headers });
    if (this.fail) throw new TypeError("network down");
    if (this.status) return new Response(null, { status: this.status });
    if (headers["If-None-Match"] === this.etag) return new Response(null, { status: 304, headers: { ETag: this.etag } });
    return new Response(JSON.stringify(this.payload), { status: 200, headers: { ETag: this.etag, "Content-Type": "application/json" } });
  };
}

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((e: { data: string }) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (e: { data: string }) => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  emit(type: string, data: string) {
    for (const l of this.listeners[type] ?? []) l({ data });
  }
  close() {
    this.closed = true;
  }
}

let server: FakeServer;
beforeEach(() => {
  server = new FakeServer();
  FakeEventSource.instances = [];
});
afterEach(() => {
  vi.useRealTimers();
});

const make = (overrides: Partial<ConstructorParameters<typeof FlagClient>[0]> = {}) =>
  new FlagClient({
    baseUrl: "https://flags.example.com/",
    sdkKey: "cli-test",
    context: { key: "u1", attributes: { country: "BD" } },
    fetch: server.fetch,
    eventSource: (url) => new FakeEventSource(url),
    ...overrides,
  });

describe("FlagClient", () => {
  it("rejects server keys", () => {
    expect(() => make({ sdkKey: "srv-secret" })).toThrow(/client-side/);
  });

  it("encodes the context as base64url without padding", () => {
    const encoded = encodeContext({ key: "u1", attributes: { country: "BD" } });
    expect(encoded).not.toMatch(/[+/=]/);
    const json = Buffer.from(encoded.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString();
    expect(JSON.parse(json)).toEqual({ key: "u1", attributes: { country: "BD" } });
    expect(encodeContext({ key: "k" })).toBe(encodeContext({ key: "k", attributes: {} }));
  });

  it("serves defaults and bootstrap values before init", () => {
    const client = make({ bootstrap: { banner: "Hello" } });
    expect(client.ready).toBe(false);
    expect(client.variation("new-checkout", false)).toBe(false);
    expect(client.variation("banner", "")).toBe("Hello");
    expect(client.allFlags()).toEqual({ banner: "Hello" });
  });

  it("fetches with the SDK key and context, then evaluates", async () => {
    const client = make({ streaming: false, pollIntervalMs: 0 });
    const ready = vi.fn();
    client.on("ready", ready);
    await client.init();
    expect(client.ready).toBe(true);
    expect(client.version).toBe(1);
    expect(server.requests[0]!.headers.Authorization).toBe("Bearer cli-test");
    expect(server.requests[0]!.url).toBe(`https://flags.example.com/v1/sdk/flags?context=${encodeContext({ key: "u1", attributes: { country: "BD" } })}`);
    expect(client.variation("new-checkout", false)).toBe(true);
    expect(client.isEnabled("new-checkout")).toBe(true);
    expect(client.variation("banner", "")).toBe("Eid Mubarak");
    expect(client.variation("missing", 42)).toBe(42);
    expect(client.detail("banner")?.reason).toEqual({ kind: "RULE_MATCH", rule_id: "bd", rule_index: 0 });
    expect(ready).toHaveBeenCalledTimes(1);
  });

  it("sends If-None-Match and keeps values on 304", async () => {
    const client = make({ streaming: false, pollIntervalMs: 0 });
    await client.init();
    const changed = await client.refresh();
    expect(changed).toBe(false);
    expect(server.requests[1]!.headers["If-None-Match"]).toBe('"v1"');
    expect(client.version).toBe(1);
  });

  it("emits change with the keys that actually changed", async () => {
    const client = make({ streaming: false, pollIntervalMs: 0 });
    await client.init();
    const onChange = vi.fn();
    client.on("change", onChange);
    server.payload = payload(2, false);
    server.etag = '"v2"';
    expect(await client.refresh()).toBe(true);
    expect(onChange).toHaveBeenCalledWith(["new-checkout"], expect.objectContaining({ version: 2 }));
    expect(client.variation("new-checkout", true)).toBe(false);
    server.payload = payload(3, false);
    server.etag = '"v3"';
    expect(await client.refresh()).toBe(false); // same values, new version: no change event
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("reports errors and keeps the last good payload", async () => {
    const client = make({ streaming: false, pollIntervalMs: 0 });
    const errors: Error[] = [];
    client.on("error", (e) => errors.push(e));
    await client.init();
    server.status = 503;
    expect(await client.refresh()).toBe(false);
    server.status = null;
    server.fail = true;
    expect(await client.refresh()).toBe(false);
    expect(errors.map((e) => e.message)).toEqual(["flags request failed with HTTP 503", "network down"]);
    expect(client.variation("new-checkout", false)).toBe(true);
  });

  it("falls back to ERROR-reason defaults", async () => {
    server.payload = { ...payload(1, true), flags: { broken: { value: null, variation: null, reason: { kind: "ERROR", error: "bad" }, version: 1 } } };
    const client = make({ streaming: false, pollIntervalMs: 0 });
    await client.init();
    expect(client.variation("broken", "fallback")).toBe("fallback");
  });

  it("opens an SSE stream after init and applies put events", async () => {
    const client = make();
    const onChange = vi.fn();
    client.on("change", onChange);
    await client.init();
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0]!;
    expect(source.url).toContain("/v1/sdk/stream?context=");
    expect(source.url).toContain("sdk_key=cli-test");
    source.emit("put", JSON.stringify(payload(5, false, "Shubho Noboborsho")));
    expect(onChange).toHaveBeenCalledWith(["banner", "new-checkout"], expect.objectContaining({ version: 5 }));
    expect(client.variation("banner", "")).toBe("Shubho Noboborsho");
    client.close();
    expect(source.closed).toBe(true);
  });

  it("surfaces malformed stream events and disconnects as errors", async () => {
    const client = make();
    const errors: Error[] = [];
    client.on("error", (e) => errors.push(e));
    await client.init();
    const source = FakeEventSource.instances[0]!;
    source.emit("put", "{not json");
    source.emit("error", "");
    expect(errors).toHaveLength(2);
    expect(errors[1]!.message).toMatch(/disconnected/);
  });

  it("identify re-fetches for the new context and reconnects the stream", async () => {
    const client = make();
    await client.init();
    const first = FakeEventSource.instances[0]!;
    server.payload = payload(2, false);
    server.etag = '"v2"';
    await client.identify({ key: "u2" });
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(server.requests[1]!.url).toContain(`context=${encodeContext({ key: "u2" })}`);
    expect(server.requests[1]!.headers["If-None-Match"]).toBeUndefined();
    expect(client.variation("new-checkout", true)).toBe(false);
  });

  it("polls on an interval when streaming is off", async () => {
    vi.useFakeTimers();
    const client = make({ streaming: false, pollIntervalMs: 1000 });
    await client.init();
    expect(server.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2500);
    expect(server.requests).toHaveLength(3);
    client.close();
    await vi.advanceTimersByTimeAsync(5000);
    expect(server.requests).toHaveLength(3);
  });

  it("unsubscribes listeners", async () => {
    const client = make({ streaming: false, pollIntervalMs: 0 });
    const onChange = vi.fn();
    const off = client.on("change", onChange);
    await client.init();
    off();
    server.payload = payload(2, false);
    server.etag = '"v2"';
    await client.refresh();
    expect(onChange).not.toHaveBeenCalled();
  });
});
