import React from "react";
import { vi } from "vitest";

vi.mock("../../main", () => ({
  GlobalContext: (() => {
    const React = require("react");
    return React.createContext({ state: {}, setState: vi.fn() });
  })(),
}));

import { appendAgentEvent } from "../App";

describe("appendAgentEvent (tool dedupe)", () => {
  test("merges when tool id appears after proposal", () => {
    const proposed = {
      type: "tool",
      name: "search",
      args: { b: 2, a: 1 },
      status: "proposed",
      chain_id: "chain-1",
      agent_id: "agent-1",
      timestamp: 1,
    };

    let events = appendAgentEvent([], proposed);
    expect(events).toHaveLength(1);

    const invoked = {
      ...proposed,
      id: "req-123",
      args: { a: 1, b: 2 },
      status: "invoked",
      result: { status: "invoked", ok: true, message: null, data: { ok: true } },
      timestamp: 2,
    };

    events = appendAgentEvent(events, invoked);
    expect(events).toHaveLength(1);
    expect(events[0].id).toBe("req-123");
    expect(events[0].status).toBe("invoked");
    expect(events[0].result).toEqual({
      status: "invoked",
      ok: true,
      message: null,
      data: { ok: true },
    });
  });

  test("does not merge distinct tool ids", () => {
    const first = {
      type: "tool",
      id: "req-1",
      name: "search",
      args: { q: "x" },
      status: "proposed",
      chain_id: "chain-1",
      agent_id: "agent-1",
      timestamp: 1,
    };

    let events = appendAgentEvent([], first);
    const second = { ...first, id: "req-2", timestamp: 2 };
    events = appendAgentEvent(events, second);
    expect(events).toHaveLength(2);
  });
});
