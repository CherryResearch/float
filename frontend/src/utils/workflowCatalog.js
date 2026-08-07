export const FALLBACK_WORKFLOW_PROFILES = Object.freeze([
  {
    id: "default",
    label: "Default",
    description: "Balanced guidance for ordinary foreground chat.",
    role: "general",
    profile_kind: "foreground",
    guidance_style: "balanced",
    latency_tier: "interactive",
    thinking_default: "auto",
    selectable_in_chat: true,
    selectable_as_default: true,
    automatic_delegation: false,
    tool_scope: "global",
    module_scope: "global",
    allow_continue_to: ["default", "mini_execution"],
    supports_background: false,
    supports_live: false,
    enabled_modules: [],
  },
  {
    id: "architect_planner",
    label: "Architect / Planner",
    description:
      "Planning-oriented guidance with high default reasoning for foreground chat.",
    role: "architect",
    profile_kind: "foreground",
    guidance_style: "planning",
    latency_tier: "deliberate",
    thinking_default: "high",
    selectable_in_chat: true,
    selectable_as_default: true,
    automatic_delegation: false,
    tool_scope: "global",
    module_scope: "global",
    allow_continue_to: ["architect_planner", "default", "mini_execution"],
    supports_background: false,
    supports_live: false,
    enabled_modules: [],
  },
  {
    id: "mini_execution",
    label: "Mini Execution",
    description:
      "Concise execution guidance with low default reasoning for foreground turns.",
    role: "worker",
    profile_kind: "foreground",
    guidance_style: "execution",
    latency_tier: "fast",
    thinking_default: "low",
    selectable_in_chat: true,
    selectable_as_default: true,
    automatic_delegation: false,
    tool_scope: "global",
    module_scope: "global",
    allow_continue_to: ["mini_execution"],
    supports_background: false,
    supports_live: false,
    enabled_modules: [],
  },
  {
    id: "background_reflection",
    label: "Background Reflection",
    description: "System-only reflection guidance used by the background service.",
    role: "background",
    profile_kind: "system",
    guidance_style: "reflection",
    latency_tier: "deliberate",
    thinking_default: "low",
    selectable_in_chat: false,
    selectable_as_default: false,
    automatic_delegation: false,
    tool_scope: "global",
    module_scope: "global",
    allow_continue_to: ["background_reflection", "mini_execution", "default"],
    supports_background: true,
    supports_live: false,
    enabled_modules: [],
  },
]);

const normalizedBoolean = (value, fallback) =>
  typeof value === "boolean" ? value : fallback;

const normalizeProfile = (profile) => {
  if (!profile || typeof profile !== "object") return null;
  const id = String(profile.id || "").trim();
  if (!id) return null;
  const profileKind =
    String(profile.profile_kind || "").trim().toLowerCase() ||
    (id === "background_reflection" ? "system" : "foreground");
  const foreground = profileKind !== "system";
  return {
    ...profile,
    id,
    label: String(profile.label || id).trim() || id,
    description: String(profile.description || "").trim(),
    profile_kind: profileKind,
    guidance_style:
      String(profile.guidance_style || "").trim().toLowerCase() || "balanced",
    selectable_in_chat: normalizedBoolean(profile.selectable_in_chat, foreground),
    selectable_as_default: normalizedBoolean(
      profile.selectable_as_default,
      foreground,
    ),
    automatic_delegation: normalizedBoolean(profile.automatic_delegation, false),
    tool_scope: String(profile.tool_scope || "global").trim() || "global",
    module_scope: String(profile.module_scope || "global").trim() || "global",
  };
};

export const normalizeWorkflowProfiles = (payload) => {
  const source = Array.isArray(payload) ? payload : payload?.workflows;
  if (!Array.isArray(source)) return [...FALLBACK_WORKFLOW_PROFILES];

  const seen = new Set();
  const profiles = source
    .map(normalizeProfile)
    .filter((profile) => {
      if (!profile || seen.has(profile.id)) return false;
      seen.add(profile.id);
      return true;
    });

  return profiles.length ? profiles : [...FALLBACK_WORKFLOW_PROFILES];
};

export const isWorkflowSelectableInChat = (profile) =>
  Boolean(
    profile &&
      profile.profile_kind !== "system" &&
      profile.selectable_in_chat !== false,
  );

export const isWorkflowSelectableAsDefault = (profile) =>
  Boolean(
    profile &&
      profile.profile_kind !== "system" &&
      profile.selectable_as_default !== false,
  );

export const resolveSelectableWorkflowId = (
  profiles,
  requestedId,
  { selection = "chat", allowUnknown = false } = {},
) => {
  const source = Array.isArray(profiles) ? profiles : [];
  const requested = String(requestedId || "").trim() || "default";
  const isSelectable =
    selection === "default"
      ? isWorkflowSelectableAsDefault
      : isWorkflowSelectableInChat;
  const requestedProfile = source.find((profile) => profile?.id === requested);
  if (requestedProfile && isSelectable(requestedProfile)) return requested;
  if (!requestedProfile && allowUnknown) return requested;
  return (
    source.find((profile) => profile?.id === "default" && isSelectable(profile))
      ?.id || source.find(isSelectable)?.id || "default"
  );
};
