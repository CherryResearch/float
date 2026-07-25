import { describe, expect, it } from "vitest";

import {
  FALLBACK_WORKFLOW_PROFILES,
  normalizeWorkflowProfiles,
} from "../workflowCatalog";

describe("normalizeWorkflowProfiles", () => {
  it("keeps valid dynamic profiles and removes duplicate or empty ids", () => {
    expect(
      normalizeWorkflowProfiles({
        workflows: [
          { id: "default", label: "Default" },
          { id: "background_reflection", label: "Background Reflection" },
          { id: "background_reflection", label: "Duplicate" },
          { id: "", label: "Missing id" },
        ],
      }),
    ).toEqual([
      { id: "default", label: "Default", description: "" },
      {
        id: "background_reflection",
        label: "Background Reflection",
        description: "",
      },
    ]);
  });

  it("falls back when the catalog is unavailable or empty", () => {
    expect(normalizeWorkflowProfiles(null)).toEqual(FALLBACK_WORKFLOW_PROFILES);
    expect(normalizeWorkflowProfiles({ workflows: [] })).toEqual(
      FALLBACK_WORKFLOW_PROFILES,
    );
  });
});
