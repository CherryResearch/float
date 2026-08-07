import { describe, expect, it } from "vitest";

import {
  FALLBACK_WORKFLOW_PROFILES,
  isWorkflowSelectableAsDefault,
  isWorkflowSelectableInChat,
  normalizeWorkflowProfiles,
  resolveSelectableWorkflowId,
} from "../workflowCatalog";

describe("normalizeWorkflowProfiles", () => {
  it("keeps valid dynamic profiles and removes duplicate or empty ids", () => {
    const profiles = normalizeWorkflowProfiles({
      workflows: [
        { id: "default", label: "Default" },
        { id: "background_reflection", label: "Background Reflection" },
        { id: "background_reflection", label: "Duplicate" },
        { id: "", label: "Missing id" },
      ],
    });

    expect(profiles).toHaveLength(2);
    expect(profiles[0]).toMatchObject({
      id: "default",
      profile_kind: "foreground",
      guidance_style: "balanced",
      selectable_in_chat: true,
      selectable_as_default: true,
      automatic_delegation: false,
      tool_scope: "global",
      module_scope: "global",
    });
    expect(profiles[1]).toMatchObject({
      id: "background_reflection",
      profile_kind: "system",
      selectable_in_chat: false,
      selectable_as_default: false,
    });
  });

  it("falls back when the catalog is unavailable or empty", () => {
    expect(normalizeWorkflowProfiles(null)).toEqual(FALLBACK_WORKFLOW_PROFILES);
    expect(normalizeWorkflowProfiles({ workflows: [] })).toEqual(
      FALLBACK_WORKFLOW_PROFILES,
    );
  });

  it("keeps the fallback contract aligned with the backend catalog", () => {
    expect(FALLBACK_WORKFLOW_PROFILES.map((profile) => profile.id)).toEqual([
      "default",
      "architect_planner",
      "mini_execution",
      "background_reflection",
    ]);
    expect(FALLBACK_WORKFLOW_PROFILES).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "default",
          thinking_default: "auto",
          allow_continue_to: ["default", "mini_execution"],
        }),
        expect.objectContaining({
          id: "background_reflection",
          profile_kind: "system",
          guidance_style: "reflection",
          supports_background: true,
          selectable_in_chat: false,
          selectable_as_default: false,
        }),
      ]),
    );
    for (const profile of FALLBACK_WORKFLOW_PROFILES) {
      expect(profile).toMatchObject({
        automatic_delegation: false,
        tool_scope: "global",
        module_scope: "global",
        enabled_modules: [],
      });
    }
  });

  it("filters system workflows and resolves unsafe saved selections to default", () => {
    const background = FALLBACK_WORKFLOW_PROFILES.find(
      (profile) => profile.id === "background_reflection",
    );

    expect(isWorkflowSelectableInChat(background)).toBe(false);
    expect(isWorkflowSelectableAsDefault(background)).toBe(false);
    expect(
      resolveSelectableWorkflowId(
        FALLBACK_WORKFLOW_PROFILES,
        "background_reflection",
      ),
    ).toBe("default");
    expect(
      resolveSelectableWorkflowId(
        FALLBACK_WORKFLOW_PROFILES,
        "background_reflection",
        { selection: "default" },
      ),
    ).toBe("default");
    expect(
      resolveSelectableWorkflowId(FALLBACK_WORKFLOW_PROFILES, "future_profile", {
        allowUnknown: true,
      }),
    ).toBe("future_profile");
  });
});
