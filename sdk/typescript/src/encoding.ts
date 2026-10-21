import type { FlagContext } from "./types";

/** base64url without padding, the format the service expects in `?context=`. */
export function encodeContext(context: FlagContext): string {
  const json = JSON.stringify({ key: context.key, attributes: context.attributes ?? {} });
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const base64 = typeof btoa === "function" ? btoa(binary) : Buffer.from(binary, "binary").toString("base64");
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function diffKeys<T>(before: Record<string, T>, after: Record<string, T>, equal: (a: T, b: T) => boolean): string[] {
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const changed: string[] = [];
  for (const key of keys) {
    const a = before[key];
    const b = after[key];
    if (a === undefined || b === undefined || !equal(a, b)) changed.push(key);
  }
  return changed.sort();
}
