export const FALLBACK_WORKFLOW_PROFILES = Object.freeze([
  {
    id: "default",
    label: "Default",
    description: "Balanced workflow for normal chat, tools, and follow-up work.",
    thinking_default: "medium",
    preferred_continue: "mini_execution",
    allow_continue_to: ["default", "architect_planner", "mini_execution"],
    enabled_modules: ["computer_use"],
  },
  {
    id: "architect_planner",
    label: "Architect / Planner",
    description: "Planning-first workflow for larger changes and explicit handoffs.",
    thinking_default: "high",
    preferred_continue: "mini_execution",
    allow_continue_to: ["architect_planner", "default", "mini_execution"],
    enabled_modules: ["computer_use"],
  },
  {
    id: "mini_execution",
    label: "Mini Execution",
    description: "Short, low-latency execution bursts between tool steps.",
    thinking_default: "low",
    preferred_continue: "mini_execution",
    allow_continue_to: ["mini_execution"],
    enabled_modules: ["computer_use"],
  },
]);

const normalizeProfile = (profile) => {
  if (!profile || typeof profile !== "object") return null;
  const id = String(profile.id || "").trim();
  if (!id) return null;
  return {
    ...profile,
    id,
    label: String(profile.label || id).trim() || id,
    description: String(profile.description || "").trim(),
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
