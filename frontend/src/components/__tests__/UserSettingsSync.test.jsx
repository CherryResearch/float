import React, { useContext } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("axios", () => ({ default: requests }));
vi.mock("../App", () => ({ default: () => null }));
vi.mock("../../utils/push", () => ({ ensureServiceWorker: vi.fn() }));

import { GlobalContext, GlobalProvider } from "../../main";

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

let changeState;
let readSettings;
let readThemes;
const savedSettings = {
  approval_level: "all",
  theme: "light",
  visual_theme: "spring",
  capture_allow_model_raw_image_access: false,
  capture_allow_summary_fallback: false,
  enabled_workflow_modules: ["saved-module"],
};
const Probe = () => {
  const { state, setState } = useContext(GlobalContext);
  changeState = (patch) => setState((previous) => ({ ...previous, ...patch }));
  return <output data-testid="preferences">{JSON.stringify(state)}</output>;
};
const preferences = () => JSON.parse(screen.getByTestId("preferences").textContent);
const saves = () => requests.post.mock.calls.filter(([url]) => url === "/api/user-settings");
const loads = () => requests.get.mock.calls.filter(([url]) => url === "/api/user-settings");
const advance = async (milliseconds = 0) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
};
const mount = async ({ strict = false } = {}) => {
  const content = <GlobalProvider><Probe /></GlobalProvider>;
  const view = render(strict ? <React.StrictMode>{content}</React.StrictMode> : content);
  await advance();
  return view;
};

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  sessionStorage.clear();
  requests.get.mockReset();
  requests.post.mockReset().mockResolvedValue({ data: { status: "saved" } });
  readSettings = () => Promise.resolve({ data: savedSettings });
  readThemes = () => Promise.resolve({ data: { themes: [] } });
  requests.get.mockImplementation((url) => {
    if (url === "/api/user-settings") return readSettings();
    if (url === "/api/themes") return readThemes();
    if (url === "/api/health") return Promise.resolve({ data: { status: "healthy" } });
    if (url === "/api/openai/models") return Promise.resolve({ data: { models: [] } });
    if (url === "/api/settings") return Promise.resolve({ data: {} });
    if (url.startsWith("/api/conversations/")) return Promise.resolve({ data: { messages: [] } });
    return Promise.reject(new Error(`Unexpected fixture request: ${url}`));
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("persisted user preferences", () => {
  it("keeps autosave disabled after a failed read, then retries without losing edits", async () => {
    let available = false;
    readSettings = () => available
      ? Promise.resolve({ data: savedSettings })
      : Promise.reject(new Error("Backend temporarily unavailable"));
    await mount();
    act(() => changeState({ theme: "dark" }));
    await advance(500);
    expect(saves()).toHaveLength(0);

    available = true;
    await advance(3000);
    expect(loads().length).toBeGreaterThan(1);
    expect(preferences()).toMatchObject({ theme: "dark", captureAllowModelRawImageAccess: false });
    await advance(500);
    expect(saves().map(([, payload]) => payload)).toEqual([{ theme: "dark" }]);
  });

  it("hydrates preferences independently when the theme catalog fails", async () => {
    readThemes = () => Promise.reject(new Error("Theme catalog temporarily unavailable"));
    await mount();
    await advance(500);
    expect(preferences()).toMatchObject({
      captureAllowModelRawImageAccess: false,
      captureAllowSummaryFallback: false,
      enabledWorkflowModules: ["saved-module"],
    });
    expect(saves()).toHaveLength(0);
  });

  it.each(["api", "local", "server"])("loads and saves preferences in %s mode", async (mode) => {
    localStorage.setItem("backendMode", mode);
    await mount();
    expect(preferences().captureAllowModelRawImageAccess).toBe(false);
    await advance(500);
    expect(saves()).toHaveLength(0);
    act(() => changeState({ theme: "dark" }));
    await advance(500);
    expect(saves().map(([, payload]) => payload)).toEqual([{ theme: "dark" }]);
  });

  it("keeps edits made during a slow initial load, including a change back to the initial value", async () => {
    const pending = deferred();
    readSettings = () => pending.promise;
    await mount();
    act(() => changeState({ theme: "dark" }));
    act(() => changeState({ theme: "light", toolDisplayMode: "inline" }));
    await act(async () => pending.resolve({ data: { ...savedSettings, theme: "dark" } }));
    await advance(500);
    expect(preferences()).toMatchObject({ theme: "light", toolDisplayMode: "inline" });
    expect(saves().map(([, payload]) => payload)).toEqual([
      { theme: "light", tool_display_mode: "inline" },
    ]);
  });

  it("does not reload preferences or lose pending changes when the inference lane changes", async () => {
    await mount();
    act(() => changeState({ theme: "dark", backendMode: "local" }));
    act(() => changeState({ backendMode: "server" }));
    act(() => changeState({ backendMode: "api" }));
    await advance(1000);
    expect(preferences().theme).toBe("dark");
    expect(loads()).toHaveLength(1);
    expect(saves().map(([, payload]) => payload)).toEqual([{ theme: "dark" }]);
  });

  it("ignores a canceled initial response after Strict Mode starts a newer load", async () => {
    const stale = deferred();
    let attempt = 0;
    readSettings = () => ++attempt === 1 ? stale.promise : Promise.resolve({ data: savedSettings });
    await mount({ strict: true });
    expect(preferences().captureAllowModelRawImageAccess).toBe(false);
    await act(async () => stale.resolve({ data: { ...savedSettings, capture_allow_model_raw_image_access: true } }));
    await advance(500);
    expect(preferences().captureAllowModelRawImageAccess).toBe(false);
    expect(saves()).toHaveLength(0);
  });

  it("serializes saves and sends an edit made while the first save is pending", async () => {
    const pending = deferred();
    requests.post.mockImplementation((url) => url === "/api/user-settings" && saves().length === 1
      ? pending.promise : Promise.resolve({ data: { status: "saved" } }));
    await mount();
    act(() => changeState({ theme: "dark" }));
    await advance(500);
    act(() => changeState({ theme: "light", toolDisplayMode: "inline" }));
    await advance(500);
    expect(saves()).toHaveLength(1);
    await act(async () => pending.resolve({ data: { status: "saved" } }));
    await advance(500);
    expect(saves().map(([, payload]) => payload)).toEqual([
      { theme: "dark" },
      { theme: "light", tool_display_mode: "inline" },
    ]);
    await advance(500);
    expect(saves()).toHaveLength(2);
  });

  it("retries a failed save with the latest values even when an edit was reverted", async () => {
    const pending = deferred();
    requests.post.mockImplementation((url) => url === "/api/user-settings" && saves().length === 1
      ? pending.promise : Promise.resolve({ data: { status: "saved" } }));
    await mount();
    act(() => changeState({ captureAllowModelRawImageAccess: true }));
    await advance(500);
    act(() => changeState({ captureAllowModelRawImageAccess: false }));
    await act(async () => pending.reject(new Error("Response lost after possible write")));
    await advance(1500);
    expect(saves().map(([, payload]) => payload)).toEqual([
      { capture_allow_model_raw_image_access: true },
      { capture_allow_model_raw_image_access: false },
    ]);
    expect(preferences().captureAllowModelRawImageAccess).toBe(false);
  });

  it("preserves a saved custom theme until its independent catalog retry succeeds", async () => {
    let available = false;
    readSettings = () => Promise.resolve({ data: { ...savedSettings, visual_theme: "forest-test" } });
    const customTheme = {
      id: "forest-test", label: "Forest test",
      slots: {
        c1Light: "#112233", c1Med: "#223344", c1Dark: "#334455",
        c2Light: "#445566", c2Med: "#556677", c2Dark: "#667788",
        veryLight: "#ddeeff", veryDark: "#001122",
      },
    };
    readThemes = () => available
      ? Promise.resolve({ data: { themes: [customTheme] } })
      : Promise.reject(new Error("Theme catalog unavailable"));
    await mount();
    expect(preferences().visualTheme).toBe("forest-test");
    expect(localStorage.getItem("visualTheme")).toBe("forest-test");
    available = true;
    await advance(1500);
    expect(document.documentElement.style.getPropertyValue("--theme-c1-light")).toBe("#112233");
    expect(preferences().visualTheme).toBe("forest-test");
    expect(saves()).toHaveLength(0);
  });

  it("rejects malformed settings without treating browser defaults as saved authority", async () => {
    readSettings = () => Promise.resolve({ data: { error: "not ready" } });
    await mount();
    act(() => changeState({ theme: "dark" }));
    await advance(2500);
    expect(loads().length).toBeGreaterThan(1);
    expect(saves()).toHaveLength(0);
  });

  it("aborts pending reads on unmount and ignores late completions", async () => {
    const pending = deferred();
    readSettings = () => pending.promise;
    const view = await mount();
    const settingsSignal = loads()[0][1].signal;
    view.unmount();
    expect(settingsSignal.aborted).toBe(true);
    await act(async () => pending.resolve({ data: savedSettings }));
    await advance(5000);
    expect(saves()).toHaveLength(0);
    expect(loads()).toHaveLength(1);
  });

  it("cancels a debounced save when its provider unmounts", async () => {
    const view = await mount();
    act(() => changeState({ theme: "dark" }));
    view.unmount();
    await advance(5000);
    expect(saves()).toHaveLength(0);
  });

  it("aborts an in-flight save on unmount without scheduling a retry", async () => {
    const pending = deferred();
    requests.post.mockImplementation((url) => url === "/api/user-settings"
      ? pending.promise : Promise.resolve({ data: { status: "saved" } }));
    const view = await mount();
    act(() => changeState({ theme: "dark" }));
    await advance(500);
    const signal = saves()[0][2].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => pending.reject(new Error("Canceled response")));
    await advance(5000);
    expect(saves()).toHaveLength(1);
  });
});
