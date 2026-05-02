import { describe, expect, it, vi } from "vitest";

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {
        backendMode: "api",
        apiModel: "test-model",
        localModel: "local-model",
        serverModel: "",
      },
    }),
  };
});

import { preferredSchemaInputType } from "../ToolArgsForm";
import { validateArgsAgainstSchema } from "../ToolEditorModal";

const flexibleTaskSchema = {
  type: "object",
  required: ["title", "start_time"],
  properties: {
    title: { type: "string" },
    start_time: { type: ["number", "string", "object"] },
    duration_minutes: { type: ["integer", "number", "string"] },
  },
};

describe("ToolEditorModal schema validation", () => {
  it("accepts any declared union type for flexible task times", () => {
    expect(
      validateArgsAgainstSchema(flexibleTaskSchema, {
        title: "Check the oven",
        start_time: "tomorrow at 5pm",
      }).ok,
    ).toBe(true);

    expect(
      validateArgsAgainstSchema(flexibleTaskSchema, {
        title: "Check the oven",
        start_time: 1774241400,
      }).ok,
    ).toBe(true);

    expect(
      validateArgsAgainstSchema(flexibleTaskSchema, {
        title: "Check the oven",
        start_time: { date: "2026-04-24", time: "17:00", timezone: "America/Vancouver" },
      }).ok,
    ).toBe(true);
  });

  it("prefers a text input for flexible time unions unless a value already has a richer type", () => {
    expect(preferredSchemaInputType(["number", "string", "object"], undefined)).toBe("string");
    expect(
      preferredSchemaInputType(["number", "string", "object"], {
        date: "2026-04-24",
        time: "17:00",
      }),
    ).toBe("object");
  });

  it("still rejects values outside the declared union", () => {
    const result = validateArgsAgainstSchema(flexibleTaskSchema, {
      title: "Check the oven",
      start_time: true,
    });

    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/number, a string, or an object/i);
  });
});
