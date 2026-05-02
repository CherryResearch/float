const MEMORY_SENSITIVITY_DETAILS = {
  mundane:
    "Routine local context. Can sync, participate in default recall, and be sent to external APIs when the current action allows normal context.",
  public:
    "Safe to share broadly. Can sync, participate in default recall, and be sent to external APIs.",
  personal:
    "Private personal context. Can sync and participate in default recall, but treat it as user-private rather than broadly shareable.",
  protected:
    "Sensitive local context. Excluded from sync and external APIs by default, and omitted from default recall unless a flow explicitly allows it.",
  secret:
    "Highest-sensitivity secret. Never synced by default, never sent to external APIs, omitted from default recall, and redacted or encrypted when available.",
};

const CONVERSATION_PRIVACY_DETAILS = {
  default:
    "Default conversation privacy. Can sync and participate in default recall unless another private rule keeps it local.",
  protected: MEMORY_SENSITIVITY_DETAILS.protected,
  secret: MEMORY_SENSITIVITY_DETAILS.secret,
};

const CONVERSATION_PRIVACY_OPTIONS = [
  { value: "default", label: "default" },
  { value: "protected", label: "protected" },
  { value: "secret", label: "secret" },
];

const MEMORY_SENSITIVITY_OPTIONS = [
  "mundane",
  "public",
  "personal",
  "protected",
  "secret",
];

const CAPTURE_SENSITIVITY_OPTIONS = MEMORY_SENSITIVITY_OPTIONS.map((value) => ({
  value,
  label: value,
}));

const WORKSPACE_PRIVACY_DETAILS = {
  default:
    "Normal workspace behavior. Items can sync and participate in default recall unless a matching private rule keeps them local.",
  protected:
    `${MEMORY_SENSITIVITY_DETAILS.protected} Use this when the whole workspace should stay local unless you move items out of it.`,
  secret:
    `${MEMORY_SENSITIVITY_DETAILS.secret} Use this for credentials, recovery material, or other secrets that should stay on this device.`,
};

const WORKSPACE_PRIVACY_OPTIONS = [
  { value: "default", label: "default" },
  { value: "protected", label: "protected" },
  { value: "secret", label: "secret" },
];

const WORKSPACE_PRIVATE_PATTERNS_HELP =
  "One rule per line. Uses simple wildcard matching like gitignore-style paths, but without negate rules. Matching items stay local: excluded from sync and omitted from default recall.";

const normalizeWorkspacePrivacyMode = (value) => {
  const mode = String(value || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(WORKSPACE_PRIVACY_DETAILS, mode)
    ? mode
    : "default";
};

const normalizeWorkspacePrivatePatterns = (value) => {
  const rawItems = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(/\r?\n/)
      : [];
  const patterns = [];
  const seen = new Set();
  rawItems.forEach((item) => {
    const text = String(item || "")
      .trim()
      .replace(/\\/g, "/")
      .replace(/^\.\/+/, "");
    if (!text || text.startsWith("#")) return;
    const lowered = text.toLowerCase();
    if (seen.has(lowered)) return;
    seen.add(lowered);
    patterns.push(text);
  });
  return patterns;
};

const workspacePrivatePatternsText = (value) =>
  normalizeWorkspacePrivatePatterns(value).join("\n");

const getSensitivityTooltip = (value) => {
  const key = String(value || "").trim().toLowerCase();
  return MEMORY_SENSITIVITY_DETAILS[key] || MEMORY_SENSITIVITY_DETAILS.personal;
};

const normalizeConversationPrivacyMode = (value) => {
  const mode = String(value || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(CONVERSATION_PRIVACY_DETAILS, mode)
    ? mode
    : "default";
};

const getConversationPrivacyTooltip = (value) => {
  const key = normalizeConversationPrivacyMode(value);
  return CONVERSATION_PRIVACY_DETAILS[key];
};

const getWorkspacePrivacyTooltip = (value) => {
  const key = normalizeWorkspacePrivacyMode(value);
  return WORKSPACE_PRIVACY_DETAILS[key];
};

const workspaceSyncBlocked = (value) =>
  ["protected", "secret"].includes(normalizeWorkspacePrivacyMode(value));

export {
  CAPTURE_SENSITIVITY_OPTIONS,
  CONVERSATION_PRIVACY_DETAILS,
  CONVERSATION_PRIVACY_OPTIONS,
  MEMORY_SENSITIVITY_DETAILS,
  MEMORY_SENSITIVITY_OPTIONS,
  WORKSPACE_PRIVATE_PATTERNS_HELP,
  WORKSPACE_PRIVACY_DETAILS,
  WORKSPACE_PRIVACY_OPTIONS,
  getConversationPrivacyTooltip,
  getSensitivityTooltip,
  getWorkspacePrivacyTooltip,
  normalizeConversationPrivacyMode,
  normalizeWorkspacePrivatePatterns,
  normalizeWorkspacePrivacyMode,
  workspacePrivatePatternsText,
  workspaceSyncBlocked,
};
