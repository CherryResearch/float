import { describe, expect, it } from "vitest";

import {
  GROK_TRUST_WARNING,
  makeCustomServerPreset,
  normalizeServerPresets,
  selectedServerPreset,
  serverTrustWarning,
} from "../serverPresets";

describe("server presets", () => {
  it("normalizes saved metadata without adding secret values", () => {
    const [preset] = normalizeServerPresets([
      {
        id: "tinker",
        name: "Tinker / Inkling",
        provider: "TINKER",
        base_url: "https://tinker.example/v1",
        api_key_env: "tinker_api_key",
        api_key_set: true,
      },
    ]);

    expect(preset).toMatchObject({
      id: "tinker",
      provider: "tinker",
      api_key_env: "TINKER_API_KEY",
      api_key_set: true,
    });
    expect(preset).not.toHaveProperty("api_key");
  });

  it("warns for user-entered xAI endpoints and routed Grok models", () => {
    const base = {
      server_preset_id: "",
      server_presets: [],
      server_url: "https://api.x.ai/v1",
    };

    expect(selectedServerPreset(base)).toBeNull();
    expect(serverTrustWarning(base)).toBe(GROK_TRUST_WARNING);
    expect(
      serverTrustWarning({
        server_url: "https://api.x.ai/v1",
        server_presets: [],
      }),
    ).toBe(GROK_TRUST_WARNING);
    expect(
      serverTrustWarning({
        ...base,
        server_url: "https://router.example.test/v1",
        transformer_model: "x-ai/grok-custom",
      }),
    ).toBe(GROK_TRUST_WARNING);
  });

  it("creates an editable custom preset draft", () => {
    expect(makeCustomServerPreset(42)).toMatchObject({
      id: "custom-42",
      name: "Custom endpoint",
      base_url: "",
      builtin: false,
    });
  });
});
