import { describe, expect, it } from "vitest";

import {
  buildModelDeleteLockInspectorRows,
  buildProviderRuntimeInspectorRows,
  buildSyncOwnershipInspectorRows,
  extractStateExplanationMessage,
} from "../stateExplanations";

describe("state explanation helpers", () => {
  it("extracts structured backend error messages and delete-lock rows", () => {
    const detail = {
      message: "Could not delete gpt-oss-20b.",
      state_explanation: {
        rows: [
          { label: "Source", value: "model delete guard" },
          { label: "Evidence", value: "LM Studio still reports the model as loaded." },
        ],
      },
    };

    expect(extractStateExplanationMessage(detail)).toBe("Could not delete gpt-oss-20b.");
    expect(buildModelDeleteLockInspectorRows(detail, "gpt-oss-20b")).toEqual([
      { label: "Source", value: "model delete guard" },
      { label: "Evidence", value: "LM Studio still reports the model as loaded." },
    ]);
  });

  it("builds provider runtime rows from ownership and last-action state", () => {
    const rows = buildProviderRuntimeInspectorRows({
      providerKey: "lmstudio",
      providerLabel: "LM Studio",
      providerRuntime: {
        loaded_model: "gpt-oss-20b",
        base_url: "http://127.0.0.1:1234/v1",
        server_owned_by_float: false,
      },
      status: "external model loaded",
      ownershipWarning: "LM Studio is already running outside Float.",
      lastOperation: { label: "Last action: load#7 failed for gpt-oss-20b" },
    });

    expect(rows).toContainEqual({ label: "Source", value: "/api/llm/provider/status" });
    expect(rows).toContainEqual({ label: "Provider", value: "LM Studio" });
    expect(rows).toContainEqual({ label: "Owner", value: "outside Float" });
    expect(rows).toContainEqual({
      label: "Last action",
      value: "Last action: load#7 failed for gpt-oss-20b",
    });
  });

  it("combines sync overview rows with backend operation explanations", () => {
    const rows = buildSyncOwnershipInspectorRows({
      syncOwnershipSummary: {
        source_namespace: "QA laptop",
        default_target_label: "Peer Float",
      },
      activeOperation: {
        id: "preview-123",
        state_explanation: {
          rows: [
            { label: "Source", value: "sync operation ledger" },
            { label: "Operation", value: "preview / preview-123" },
            { label: "Next", value: "Stop records cancel intent and aborts the wait." },
          ],
        },
      },
      activeDescription: "preview running",
      lastDescription: "check completed",
    });

    expect(rows).toContainEqual({ label: "Source", value: "/api/sync/overview" });
    expect(rows).toContainEqual({ label: "Owner", value: "QA laptop" });
    expect(rows).toContainEqual({ label: "Active", value: "preview running" });
    expect(rows).toContainEqual({
      label: "Evidence",
      value: "Operation: preview / preview-123",
    });
    expect(rows).toContainEqual({
      label: "Next",
      value: "Stop records cancel intent and aborts the wait.",
    });
  });
});
