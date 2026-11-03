import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { FlagClient, encodeContext } from "../src/index";
import type { ClientPayload, EvaluationReason, FlagContext, FlagValue } from "../src/types";

/** The shared vectors the server and the Python SDK are tested against. */
interface VectorCase {
  name: string;
  flag: string;
  context: Required<FlagContext>;
  expected: { variation: string | null; value: FlagValue; reason: EvaluationReason };
}
const vectors = JSON.parse(readFileSync(resolve(__dirname, "../../../tests/vectors/evaluation.json"), "utf8")) as {
  cases: VectorCase[];
};

/** Serve what the service sends a client key for this context: the evaluated vector result. */
const serve = (vector: VectorCase) => {
  const urls: string[] = [];
  const body: ClientPayload = {
    environment: "prod",
    version: 1,
    context_key: vector.context.key,
    flags: { [vector.flag]: { ...vector.expected, version: 1 } },
  };
  const fetch = async (input: string): Promise<Response> => {
    urls.push(input);
    return new Response(JSON.stringify(body), { status: 200, headers: { ETag: '"v1"' } });
  };
  return { urls, fetch };
};

describe("shared evaluation vectors", () => {
  it("cover prerequisites and holdouts", () => {
    const kinds = new Set(vectors.cases.map((c) => c.expected.reason.kind));
    expect(kinds.has("PREREQUISITE_FAILED")).toBe(true);
    expect(vectors.cases.some((c) => c.expected.reason.in_holdout)).toBe(true);
  });

  it.each(vectors.cases.map((c) => [c.name, c] as const))("%s", async (_name, vector) => {
    const server = serve(vector);
    const client = new FlagClient({
      baseUrl: "https://flags.example.com",
      sdkKey: "cli-test",
      context: vector.context,
      fetch: server.fetch,
      streaming: false,
      pollIntervalMs: 0,
    });
    await client.init();
    expect(server.urls[0]).toContain(`context=${encodeContext(vector.context)}`);
    expect(client.detail(vector.flag)?.reason).toEqual(vector.expected.reason);
    // only ERROR falls back to the default; a failed prerequisite or a holdout is a real answer
    const fallback = "fallback";
    const expected = vector.expected.reason.kind === "ERROR" ? fallback : vector.expected.value;
    expect(client.variation(vector.flag, fallback)).toEqual(expected);
    client.close();
  });
});
