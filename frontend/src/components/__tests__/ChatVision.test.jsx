import React from "react";
import { vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";

vi.mock("../../main", () => {
  const React = require("react");
  return {
    GlobalContext: React.createContext({
      state: {},
      setState: () => {},
    }),
  };
});

const xhrRequests = vi.hoisted(() => []);

const proxyMocks = vi.hoisted(() => ({
  chat: vi.fn(),
}));

const syncMocks = vi.hoisted(() => ({
  ensureDeviceAndToken: vi.fn(),
}));

vi.mock("../../utils/proxy", () => ({
  memoryStore: {},
  apiWrapper: {
    chat: proxyMocks.chat,
  },
}));

vi.mock("../../utils/sync", () => ({
  ensureDeviceAndToken: syncMocks.ensureDeviceAndToken,
}));

vi.mock("livekit-client", () => ({
  Room: class {
    on() {}
    off() {}
    disconnect() {}
    prepareConnection() {
      return Promise.resolve();
    }
    connect() {
      return Promise.resolve();
    }
  },
  RoomEvent: {},
}));

vi.mock("../MediaViewer", () => ({
  default: () => null,
}));

vi.mock("../ToolEditorModal", () => ({
  default: () => null,
}));

vi.mock("../RagContextPanel", () => ({
  __esModule: true,
  default: () => null,
  normalizeRagMatches: (value) => value || [],
}));

import Chat from "../Chat";
import { GlobalContext } from "../../main";

let originalXMLHttpRequest;

const baseState = {
  conversation: [],
  history: [],
  sessionId: "sess-vision",
  backendMode: "api",
  apiStatus: "online",
  approvalLevel: "all",
  apiModel: "gpt-4.1-mini",
  transformerModel: "gpt-oss-20b",
  localModel: "local-model",
  thinkingMode: "auto",
};

const renderChat = (stateOverrides = {}) => {
  const state = { ...baseState, ...stateOverrides };
  const setState = vi.fn();
  const result = render(
    <MemoryRouter>
      <GlobalContext.Provider value={{ state, setState }}>
        <Chat thoughts={[]} setActiveMessageId={() => {}} />
      </GlobalContext.Provider>
    </MemoryRouter>,
  );
  return { ...result, setState };
};

describe("Chat vision integration", () => {
  afterEach(() => {
    cleanup();
    if (originalXMLHttpRequest) {
      globalThis.XMLHttpRequest = originalXMLHttpRequest;
      originalXMLHttpRequest = undefined;
    }
  });

  beforeEach(() => {
    let uuidCounter = 0;
    Object.defineProperty(globalThis, "crypto", {
      value: {
        randomUUID: () => `uuid-${++uuidCounter}`,
      },
      configurable: true,
    });
    Object.defineProperty(window, "matchMedia", {
      value: vi.fn().mockReturnValue({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
      configurable: true,
    });
    Object.defineProperty(navigator, "sendBeacon", {
      value: vi.fn(() => true),
      configurable: true,
    });
    Object.defineProperty(URL, "createObjectURL", {
      value: vi.fn(() => "blob:preview"),
      configurable: true,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      value: vi.fn(),
      configurable: true,
    });
    Object.defineProperty(HTMLMediaElement.prototype, "play", {
      value: vi.fn(() => Promise.resolve()),
      configurable: true,
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      value: vi.fn(() => ({ drawImage: vi.fn() })),
      configurable: true,
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
      value: vi.fn((callback) =>
        callback(new Blob(["camera-bytes"], { type: "image/png" })),
      ),
      configurable: true,
    });

    xhrRequests.length = 0;
    proxyMocks.chat.mockReset();
    syncMocks.ensureDeviceAndToken.mockReset();

    syncMocks.ensureDeviceAndToken.mockResolvedValue({
      id: "device-1",
      token: "token-1",
    });
    proxyMocks.chat.mockResolvedValue({
      message: "vision answer",
      thought: "",
      tools_used: [],
      metadata: {},
    });
    originalXMLHttpRequest = globalThis.XMLHttpRequest;
    class FakeXMLHttpRequest {
      constructor() {
        this.headers = {};
        this.readyState = 0;
        this.status = 0;
        this.statusText = "OK";
        this.responseText = "";
        this.response = "";
        this.responseURL = "";
        this.timeout = 0;
        this.withCredentials = false;
        this.upload = {
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        };
      }

      open(method, url) {
        this.method = method;
        this.url = url;
        this.responseURL = url;
        this.readyState = 1;
      }

      setRequestHeader(name, value) {
        this.headers[name] = value;
      }

      getAllResponseHeaders() {
        return "content-type: application/json\r\n";
      }

      addEventListener() {}

      removeEventListener() {}

      send(body) {
        this.body = body;
        xhrRequests.push(this);
        window.setTimeout(() => {
          let payload = {};
          if (this.url === "/api/attachments/upload") {
            const origin = body.get("origin") || "upload";
            const file = body.get("file");
            const hash = origin === "captured" ? "captured-hash" : "upload-hash";
            const root = origin === "captured" ? "captured" : "uploads";
            payload = {
              url: `/api/attachments/${hash}/${file.name}`,
              content_hash: hash,
              origin,
              relative_path: `${root}/${hash}/${file.name}`,
            };
          } else if (this.url === "/api/chat") {
            payload = {
              message: "vision answer",
              thought: "",
              tools_used: [],
              metadata: {},
            };
          }
          this.status = 200;
          this.readyState = 4;
          this.responseText = JSON.stringify(payload);
          this.response = this.responseText;
          if (typeof this.onreadystatechange === "function") {
            this.onreadystatechange();
          }
          if (typeof this.onloadend === "function") {
            this.onloadend();
          }
        }, 0);
      }

      abort() {
        if (typeof this.onabort === "function") {
          this.onabort();
        }
      }
    }
    globalThis.XMLHttpRequest = FakeXMLHttpRequest;
  });

  it("shows the vision selector for uploaded images and sends vision_workflow", async () => {
    renderChat();
    expect(screen.queryByLabelText("Vision mode")).not.toBeInTheDocument();

    const fileInput = document.body.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "sample.png", { type: "image/png" })],
      },
    });

    const select = await screen.findByLabelText("Vision mode");
    expect(
      screen.queryByRole("button", { name: /explain vision modes/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Vision mode"),
    ).toHaveAttribute("title", "How the image will be interpreted by the model.");
    expect(select).toHaveAttribute(
      "title",
      expect.stringContaining("How the image will be interpreted by the model."),
    );
    fireEvent.change(select, { target: { value: "caption" } });
    expect(select).toHaveAttribute(
      "title",
      expect.stringContaining("Generate a clean description"),
    );
    await waitFor(() =>
      expect(
        xhrRequests.some((request) => request.url === "/api/attachments/upload"),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(screen.queryByText(/uploading/i)).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() =>
      expect(
        proxyMocks.chat.mock.calls.length > 0 ||
          xhrRequests.some((request) => request.url === "/api/chat"),
      ).toBe(true),
    );
    const chatRequest = xhrRequests.find((request) => request.url === "/api/chat");
    const payload =
      proxyMocks.chat.mock.calls[0]?.[0] ||
      (typeof chatRequest?.body === "string" ? JSON.parse(chatRequest.body) : chatRequest?.body);
    expect(payload.vision_workflow).toBe("caption");
    expect(payload.message).toBe("Describe the attached image.");
    expect(payload.attachments).toHaveLength(1);
    expect(payload.attachments[0].origin).toBe("upload");
    expect(payload.attachments[0].relative_path).toBe(
      "uploads/upload-hash/sample.png",
    );
    await waitFor(() =>
      expect(screen.queryByLabelText("Vision mode")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("sample.png")).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview");
  });

  it("captures a camera frame and uploads it as a captured attachment", async () => {
    const stopTrack = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: stopTrack }],
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia },
      configurable: true,
    });

    renderChat();
    fireEvent.click(screen.getByRole("button", { name: /open attachments/i }));
    fireEvent.click(await screen.findByRole("button", { name: /capture from camera/i }));

    expect(getUserMedia).toHaveBeenCalledTimes(1);
    await screen.findByRole("button", { name: "capture" });
    expect(
      screen.getByRole("separator", { name: /drag to resize composer/i }),
    ).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "capture" }));

    await waitFor(() =>
      expect(
        xhrRequests.some((request) => request.url === "/api/attachments/upload"),
      ).toBe(true),
    );

    const uploadCall = xhrRequests.find(
      (request) => request.url === "/api/attachments/upload",
    );
    expect(uploadCall).toBeTruthy();
    const formData = uploadCall.body;
    expect(formData.get("origin")).toBe("captured");
    expect(formData.get("capture_source")).toBe("chat_camera");

    await waitFor(() => expect(stopTrack).toHaveBeenCalled());
    expect(await screen.findByLabelText("Vision mode")).toBeInTheDocument();
  });

  it("pastes image clipboard items into attachments", async () => {
    renderChat();

    const textarea = screen.getByPlaceholderText("Type your message...");
    const pastedFile = new File(["paste-bytes"], "pasted.png", { type: "image/png" });

    fireEvent.paste(textarea, {
      clipboardData: {
        items: [
          {
            kind: "file",
            type: "image/png",
            getAsFile: () => pastedFile,
          },
        ],
        getData: () => "",
      },
    });

    await waitFor(() =>
      expect(
        xhrRequests.some((request) => request.url === "/api/attachments/upload"),
      ).toBe(true),
    );
    expect(await screen.findByLabelText("Vision mode")).toBeInTheDocument();
    expect(screen.getByText("pasted.png")).toBeInTheDocument();
  });

  it("closes the camera preview and stops the active track", async () => {
    const stopTrack = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: stopTrack }],
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia },
      configurable: true,
    });

    renderChat();
    fireEvent.click(screen.getByRole("button", { name: /open attachments/i }));
    fireEvent.click(await screen.findByRole("button", { name: /capture from camera/i }));

    const closeButton = await screen.findByRole("button", {
      name: /close camera preview/i,
    });
    fireEvent.click(closeButton);

    await waitFor(() => expect(stopTrack).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", { name: /close camera preview/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("separator", { name: /drag to resize composer/i }),
    ).toHaveAttribute("aria-disabled", "false");
  });
});
