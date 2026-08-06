const SENSITIVE_SOURCE_QUERY_NAMES = new Set([
  "token", "access_token", "api_key", "x_api_key", "apikey", "key",
  "signature", "sig", "credential", "auth", "authorization", "password",
  "passwd", "pwd", "secret", "client_secret", "bearer", "bearer_token",
  "session", "session_id", "jwt", "jwt_token", "id_token", "refresh_token",
  "client_assertion", "assertion", "googleaccessid", "awsaccesskeyid", "policy",
  // Azure shared-access-signature fields.
  "se", "sp", "sr", "st", "sv", "sip", "spr", "skoid", "sktid", "skt",
  "ske", "sks", "skv",
]);
const SENSITIVE_SOURCE_COMPACT_QUERY_NAMES = new Set(
  [...SENSITIVE_SOURCE_QUERY_NAMES].map((name) => name.replaceAll("_", "")),
);

const decodeSourceKey = (value) => {
  let decoded = String(value || "");
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    } catch {
      return "";
    }
  }
  return decoded;
};

const sourceKeyContainsCredentials = (key) => {
  const decoded = decodeSourceKey(key).replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  const normalized = decoded.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const compact = normalized.replaceAll("_", "");
  return (
    normalized.startsWith("x_amz_")
    || normalized.startsWith("x_goog_")
    || normalized.startsWith("jwt_")
    || normalized.startsWith("bearer_")
    || ["password", "secret", "credential", "signature", "token", "jwt", "session"]
      .some((suffix) => normalized.endsWith(`_${suffix}`))
    || SENSITIVE_SOURCE_QUERY_NAMES.has(normalized)
    || SENSITIVE_SOURCE_COMPACT_QUERY_NAMES.has(compact)
  );
};

const sourceUrlContainsCredentials = (url) => {
  for (const key of url.searchParams.keys()) {
    if (sourceKeyContainsCredentials(key)) return true;
  }
  if (!url.hash) return false;
  let fragment = url.hash.slice(1);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const next = decodeURIComponent(fragment);
      if (next === fragment) break;
      fragment = next;
    } catch {
      return true;
    }
  }
  const queryLikeFragment = fragment.includes("?")
    ? fragment.slice(fragment.indexOf("?") + 1)
    : fragment;
  for (const key of new URLSearchParams(queryLikeFragment).keys()) {
    if (sourceKeyContainsCredentials(key)) return true;
  }
  return false;
};

// Source URLs are passive, potentially stale provenance. They are never a
// substitute for Float's content hash, managed path, or retrieval route.
export const safeAttachmentSourceUrl = (value) => {
  if (typeof value !== "string" || !value.trim()) return "";
  try {
    const parsed = new URL(value.trim());
    return (parsed.protocol === "http:" || parsed.protocol === "https:")
      && !parsed.username
      && !parsed.password
      && !sourceUrlContainsCredentials(parsed)
      ? parsed.toString()
      : "";
  } catch {
    return "";
  }
};
