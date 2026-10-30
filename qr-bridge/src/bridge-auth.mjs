/** Fail closed before timing-safe comparison: no configured key is not a credential. */
import {timingSafeEqual} from "node:crypto";

export function bridgeKeyAuthorized(header, configuredKey) {
  if (typeof configuredKey !== "string" || typeof header !== "string") return false;
  const expected = Buffer.from(configuredKey, "utf8");
  if (expected.length < 32 || expected.length > 256) return false;
  const provided = Buffer.from(header, "utf8");
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}
