# feature-flags-client

Typed client for [feature-flags-service](../../README.md) for browsers and Node 18+.

```ts
import { FlagClient } from "feature-flags-client";

const flags = new FlagClient({
  baseUrl: "https://flags.example.com",
  sdkKey: "cli-…",                                   // client-side key only
  context: { key: user.id, attributes: { country: "BD", plan: user.plan } },
  bootstrap: { "new-checkout": false },               // served until the first fetch lands
});

await flags.init();                                   // GET /v1/sdk/flags, then SSE /v1/sdk/stream
flags.isEnabled("new-checkout");                      // false | true
flags.variation("banner", "");                        // typed default
flags.on("change", (keys) => rerender(keys));         // only keys whose value changed
await flags.identify({ key: "u2" });                  // after login
flags.close();
```

Build: `npm run build` (tsup, ESM + CJS + `.d.ts`). Test: `npm test` (vitest).
