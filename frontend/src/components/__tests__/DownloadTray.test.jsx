import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import DownloadTray from "../DownloadTray";

class MockBroadcastChannel {
  constructor() {
    this.onmessage = null;
  }

  postMessage() {}

  close() {}
}

class MockMutationObserver {
  constructor(callback) {
    this.callback = callback;
  }

  observe() {}

  disconnect() {}
}

describe("DownloadTray", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("downloadTrayExpanded", "true");
    Object.defineProperty(globalThis, "BroadcastChannel", {
      value: MockBroadcastChannel,
      configurable: true,
    });
    Object.defineProperty(globalThis, "MutationObserver", {
      value: MockMutationObserver,
      configurable: true,
    });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    document.body.innerHTML = "";
  });

  it("marks the tray as composer-aware when the full input bubble is present", async () => {
    const marker = document.createElement("div");
    marker.className = "input-box";
    document.body.appendChild(marker);

    render(<DownloadTray />);

    await waitFor(() =>
      expect(document.querySelector(".download-tray.with-input-box")).toBeInTheDocument(),
    );
  });

  it("marks the tray separately when only the collapsed chat button is visible", async () => {
    const marker = document.createElement("button");
    marker.className = "open-entry-btn";
    document.body.appendChild(marker);

    render(<DownloadTray />);

    await waitFor(() =>
      expect(
        document.querySelector(".download-tray.with-entry-button"),
      ).toBeInTheDocument(),
    );
  });
});
