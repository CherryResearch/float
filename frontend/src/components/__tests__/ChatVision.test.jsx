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
const attachmentOutboxState = vi.hoisted(() => ({ entries: new Map() }));

const attachmentOutboxMocks = vi.hoisted(() => ({
  cleanup: vi.fn(),
  put: vi.fn(),
  list: vi.fn(),
  deleteOne: vi.fn(),
  deleteSent: vi.fn(),
}));

const proxyMocks = vi.hoisted(() => ({
  chat: vi.fn(),
}));

vi.mock("../../utils/proxy", () => ({
  debugLog: vi.fn(),
  getConversationMessageLimit: vi.fn(() => 400),
  memoryStore: {},
  apiWrapper: {
    chat: proxyMocks.chat,
  },
}));

vi.mock("../../utils/attachmentOutbox", () => ({
  cleanupExpiredAttachmentOutboxEntries: attachmentOutboxMocks.cleanup,
  putAttachmentOutboxEntry: attachmentOutboxMocks.put,
  listAttachmentOutboxEntries: attachmentOutboxMocks.list,
  deleteAttachmentOutboxEntry: attachmentOutboxMocks.deleteOne,
  deleteSentAttachmentOutboxEntries: attachmentOutboxMocks.deleteSent,
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
import { getMessageVisionNotice } from "../../utils/visionDelivery";

let originalXMLHttpRequest;
let holdNextAttachmentUpload = false;
let failNextAttachmentUploads = 0;
let malformedNextAttachmentUploads = 0;

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

const chatElement = (stateOverrides = {}) => {
  const state = { ...baseState, ...stateOverrides };
  const setState = vi.fn();
  return {
    element: (
      <MemoryRouter>
        <GlobalContext.Provider value={{ state, setState }}>
          <Chat thoughts={[]} setActiveMessageId={() => {}} />
        </GlobalContext.Provider>
      </MemoryRouter>
    ),
    setState,
  };
};

const renderChat = (stateOverrides = {}) => {
  const { element, setState } = chatElement(stateOverrides);
  const result = render(element);
  return { ...result, setState };
};

describe("Chat vision integration", () => {
  it("makes unavailable visual input explicit to the user", () => {
    renderChat({
      conversation: [
        {
          id: "msg-image:user",
          role: "user",
          text: "What is in this image?",
          attachments: [{ name: "sample.png", type: "image/png", url: "/sample.png" }],
        },
        {
          id: "msg-image",
          role: "ai",
          text: "I cannot determine that from the available context.",
          metadata: {
            vision: {
              native_image_input: false,
              fallback_used: true,
              fallback_images: 1,
              fallback_attachments: [{ name: "sample.png", placeholder: true }],
            },
          },
        },
      ],
    });

    const notice = screen.getByRole("status", { name: "Image delivery notice" });
    expect(notice).toHaveTextContent("Image not seen");
    expect(notice).toHaveTextContent("selected model did not receive visual content");
    expect(notice).toHaveTextContent("reply may rely only on your text");
  });

  it("does not warn when the selected model received native image input", () => {
    expect(
      getMessageVisionNotice({
        role: "ai",
        metadata: {
          vision: {
            native_image_input: true,
            fallback_used: false,
          },
        },
      }),
    ).toBeNull();
  });

  afterEach(() => {
    cleanup();
    sessionStorage.clear();
    attachmentOutboxState.entries.clear();
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
    sessionStorage.clear();
    attachmentOutboxState.entries.clear();
    Object.values(attachmentOutboxMocks).forEach((mock) => mock.mockReset());
    attachmentOutboxMocks.cleanup.mockResolvedValue(0);
    attachmentOutboxMocks.put.mockImplementation(async (sessionId, entry) => {
      const key = `${sessionId}:${entry.id}`;
      const stored = {
        ...(attachmentOutboxState.entries.get(key) || {}),
        ...entry,
        sessionId,
      };
      attachmentOutboxState.entries.set(key, stored);
      return stored;
    });
    attachmentOutboxMocks.list.mockImplementation(async (sessionId) =>
      Array.from(attachmentOutboxState.entries.values()).filter(
        (entry) => entry.sessionId === sessionId,
      ));
    attachmentOutboxMocks.deleteOne.mockImplementation(
      async (sessionId, attachmentId) =>
        attachmentOutboxState.entries.delete(`${sessionId}:${attachmentId}`),
    );
    attachmentOutboxMocks.deleteSent.mockImplementation(
      async (sessionId, attachmentIds = []) => {
        let deleted = 0;
        attachmentIds.forEach((attachmentId) => {
          if (attachmentOutboxState.entries.delete(`${sessionId}:${attachmentId}`)) {
            deleted += 1;
          }
        });
        return deleted;
      },
    );
    holdNextAttachmentUpload = false;
    failNextAttachmentUploads = 0;
    malformedNextAttachmentUploads = 0;
    proxyMocks.chat.mockReset();
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
        if (this.url === "/api/attachments/upload" && holdNextAttachmentUpload) {
          holdNextAttachmentUpload = false;
          return;
        }
        window.setTimeout(() => {
          if (this.url === "/api/attachments/upload" && failNextAttachmentUploads > 0) {
            failNextAttachmentUploads -= 1;
            this.status = 503;
            this.statusText = "Service Unavailable";
            this.readyState = 4;
            this.responseText = JSON.stringify({ detail: "tailnet upload interrupted" });
            this.response = this.responseText;
            if (typeof this.onreadystatechange === "function") {
              this.onreadystatechange();
            }
            if (typeof this.onloadend === "function") {
              this.onloadend();
            }
            return;
          }
          let payload = {};
          if (
            this.url === "/api/attachments/upload" &&
            malformedNextAttachmentUploads > 0
          ) {
            malformedNextAttachmentUploads -= 1;
          } else if (this.url === "/api/attachments/upload") {
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
          } else if (this.url === "/api/captures/upload") {
            const file = body.get("file");
            payload = {
              url: "/api/captures/capture-1/content",
              content_hash: "camera-hash",
              capture_id: "capture-1",
              transient: true,
              origin: "captured",
              relative_path: `captures/transient/capture-1/${file.name}`,
              expires_at_iso: "2026-07-25T12:00:00Z",
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
    expect(payload.message).toBe("");
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

  it("restores a completed attachment after tab-scoped storage is lost", async () => {
    const view = renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "sample.png", { type: "image/png" })],
      },
    });

    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.state === "ready",
        ),
      ).toBe(true),
    );
    view.unmount();
    sessionStorage.clear();

    renderChat();
    expect(await screen.findByText("sample.png")).toBeInTheDocument();
    expect(screen.getByLabelText("Vision mode")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => expect(proxyMocks.chat).toHaveBeenCalled());
    const payload = proxyMocks.chat.mock.calls[0][0];
    expect(payload.attachments).toHaveLength(1);
    expect(payload.attachments[0].url).toContain(
      "/api/attachments/upload-hash/sample.png",
    );
    expect(payload.attachments[0].relative_path).toBe(
      "uploads/upload-hash/sample.png",
    );
    await waitFor(() => expect(attachmentOutboxState.entries.size).toBe(0));
  });

  it("restores and retries an upload interrupted by a mobile reload", async () => {
    holdNextAttachmentUpload = true;
    const view = renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "reconnect.png", { type: "image/png" })],
      },
    });

    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.state === "uploading" && entry.file,
        ),
      ).toBe(true),
    );
    expect(
      xhrRequests.filter((request) => request.url === "/api/attachments/upload"),
    ).toHaveLength(1);

    view.unmount();
    sessionStorage.clear();
    renderChat();

    expect(await screen.findByText("reconnect.png")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        xhrRequests.filter((request) => request.url === "/api/attachments/upload"),
      ).toHaveLength(2),
    );
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.state === "ready",
        ),
      ).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(proxyMocks.chat).toHaveBeenCalled());
    expect(proxyMocks.chat.mock.calls[0][0].attachments[0].url).toContain(
      "/api/attachments/upload-hash/reconnect.png",
    );
  });

  it("keeps a failed upload blocked until retry succeeds", async () => {
    failNextAttachmentUploads = 1;
    renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "retry.png", { type: "image/png" })],
      },
    });

    const retryButton = await screen.findByRole("button", { name: "Retry retry.png" });
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    expect(proxyMocks.chat).not.toHaveBeenCalled();

    fireEvent.click(retryButton);
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.state === "ready",
        ),
      ).toBe(true),
    );
    expect(
      screen.queryByRole("button", { name: "Retry retry.png" }),
    ).not.toBeInTheDocument();

    const sendButton = screen.getByRole("button", { name: /send message/i });
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);
    await waitFor(() => expect(proxyMocks.chat).toHaveBeenCalled());
    expect(proxyMocks.chat.mock.calls[0][0].attachments).toHaveLength(1);
  });

  it("treats an upload response without a URL as retryable failure", async () => {
    malformedNextAttachmentUploads = 1;
    renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "missing-url.png", { type: "image/png" })],
      },
    });

    expect(
      await screen.findByRole("button", { name: "Retry missing-url.png" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    expect(
      Array.from(attachmentOutboxState.entries.values()).some(
        (entry) => entry.name === "missing-url.png" && entry.state === "failed",
      ),
    ).toBe(true);
  });

  it("deduplicates overlapping manual and online attachment retries", async () => {
    failNextAttachmentUploads = 1;
    renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "one-retry.png", { type: "image/png" })],
      },
    });

    const retryButton = await screen.findByRole("button", {
      name: "Retry one-retry.png",
    });
    holdNextAttachmentUpload = true;
    fireEvent.click(retryButton);
    window.dispatchEvent(new Event("online"));

    await waitFor(() =>
      expect(
        xhrRequests.filter((request) => request.url === "/api/attachments/upload"),
      ).toHaveLength(2),
    );
  });

  it("does not restore an attachment removed while the outbox list is pending", async () => {
    const outboxEntry = {
      id: "race-id",
      sessionId: "sess-vision",
      state: "ready",
      descriptor: {
        id: "race-id",
        outboxId: "race-id",
        name: "race.png",
        type: "image/png",
        url: "/api/attachments/race-hash/race.png",
        remoteUrl: "/api/attachments/race-hash/race.png",
      },
    };
    attachmentOutboxState.entries.set("sess-vision:race-id", outboxEntry);
    sessionStorage.setItem(
      "float:chat-composer-draft:v1:sess-vision",
      JSON.stringify({
        message: "",
        attachments: [outboxEntry.descriptor],
        visionWorkflow: "auto",
      }),
    );
    let resolveList;
    attachmentOutboxMocks.list.mockImplementationOnce(
      () => new Promise((resolve) => { resolveList = resolve; }),
    );

    renderChat();
    expect(await screen.findByText("race.png")).toBeInTheDocument();
    const listedEntry = { ...outboxEntry };
    fireEvent.click(screen.getByRole("button", { name: "Remove race.png" }));
    resolveList([listedEntry]);

    await waitFor(() =>
      expect(screen.queryByText("race.png")).not.toBeInTheDocument(),
    );
    expect(attachmentOutboxState.entries.has("sess-vision:race-id")).toBe(false);
  });

  it("does not resurrect a file removed while the ready outbox write is pending", async () => {
    const defaultPut = attachmentOutboxMocks.put.getMockImplementation();
    let releaseReadyWrite;
    attachmentOutboxMocks.put.mockImplementation(async (sessionId, entry) => {
      const stored = await defaultPut(sessionId, entry);
      if (entry.state === "ready") {
        await new Promise((resolve) => { releaseReadyWrite = resolve; });
      }
      return stored;
    });
    renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "remove-race.png", { type: "image/png" })],
      },
    });

    await waitFor(() => expect(releaseReadyWrite).toBeTypeOf("function"));
    fireEvent.click(
      screen.getByRole("button", { name: "Remove remove-race.png" }),
    );
    releaseReadyWrite();

    await waitFor(() =>
      expect(screen.queryByText("remove-race.png")).not.toBeInTheDocument(),
    );
    expect(
      Array.from({ length: sessionStorage.length }, (_, index) =>
        sessionStorage.getItem(sessionStorage.key(index)),
      ).some((value) => value?.includes("remove-race.png")),
    ).toBe(false);
  });

  it("keeps a removed attachment hidden across reload while IndexedDB deletion is pending", async () => {
    attachmentOutboxMocks.deleteOne.mockImplementation(
      () => new Promise(() => {}),
    );
    const view = renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "delete-pending.png", { type: "image/png" })],
      },
    });
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.name === "delete-pending.png" && entry.state === "ready",
        ),
      ).toBe(true),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Remove delete-pending.png" }),
    );
    view.unmount();
    renderChat();

    await waitFor(() =>
      expect(screen.queryByText("delete-pending.png")).not.toBeInTheDocument(),
    );
    expect(
      Array.from(attachmentOutboxState.entries.values()).some(
        (entry) => entry.name === "delete-pending.png",
      ),
    ).toBe(true);
  });

  it("clears only the submitted attachment after a delayed response", async () => {
    let resolveChat;
    proxyMocks.chat.mockImplementationOnce(
      () => new Promise((resolve) => { resolveChat = resolve; }),
    );
    renderChat();
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["first"], "submitted.png", { type: "image/png" })],
      },
    });
    await waitFor(() => expect(screen.queryByText(/uploading/i)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(proxyMocks.chat).toHaveBeenCalledTimes(1));

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["second"], "new-draft.png", { type: "image/png" })],
      },
    });
    expect(await screen.findByText("new-draft.png")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.name === "new-draft.png" && entry.state === "ready",
        ),
      ).toBe(true),
    );

    resolveChat({ message: "done", thought: "", tools_used: [], metadata: {} });
    await waitFor(() =>
      expect(screen.queryByText("submitted.png")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("new-draft.png")).toBeInTheDocument();
    expect(
      Array.from(attachmentOutboxState.entries.values()).map((entry) => entry.name),
    ).toEqual(["new-draft.png"]);
  });

  it("does not clear the next session's attachments when a response finishes", async () => {
    let resolveChat;
    proxyMocks.chat.mockImplementationOnce(
      () => new Promise((resolve) => { resolveChat = resolve; }),
    );
    const view = renderChat({ sessionId: "sess-a" });
    const fileInput = document.body.querySelector('input[type="file"]');

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["a"], "sent-a.png", { type: "image/png" })],
      },
    });
    await waitFor(() => expect(screen.queryByText(/uploading/i)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(proxyMocks.chat).toHaveBeenCalledTimes(1));

    view.rerender(chatElement({ sessionId: "sess-b" }).element);
    await waitFor(() => expect(screen.queryByText("sent-a.png")).not.toBeInTheDocument());
    const sessionBInput = document.body.querySelector('input[type="file"]');
    fireEvent.change(sessionBInput, {
      target: {
        files: [new File(["b"], "draft-b.png", { type: "image/png" })],
      },
    });
    expect(await screen.findByText("draft-b.png")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.sessionId === "sess-b" && entry.state === "ready",
        ),
      ).toBe(true),
    );

    resolveChat({ message: "done", thought: "", tools_used: [], metadata: {} });
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.sessionId === "sess-a",
        ),
      ).toBe(false),
    );
    expect(screen.getByText("draft-b.png")).toBeInTheDocument();
  });

  it("isolates attachment drafts when the active session changes", async () => {
    const view = renderChat({ sessionId: "sess-a" });
    const fileInput = document.body.querySelector('input[type="file"]');
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["upload-bytes"], "session-a.png", { type: "image/png" })],
      },
    });
    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.sessionId === "sess-a" && entry.state === "ready",
        ),
      ).toBe(true),
    );

    view.rerender(chatElement({ sessionId: "sess-b" }).element);
    await waitFor(() =>
      expect(screen.queryByText("session-a.png")).not.toBeInTheDocument(),
    );
    const sessionBDrafts = Array.from({ length: sessionStorage.length }, (_, index) => ({
      key: sessionStorage.key(index),
      value: sessionStorage.getItem(sessionStorage.key(index)),
    })).filter((entry) => entry.key?.includes("sess-b"));
    expect(sessionBDrafts.some((entry) => entry.value?.includes("session-a.png"))).toBe(
      false,
    );
    expect(
      Array.from(attachmentOutboxState.entries.values()).some(
        (entry) => entry.sessionId === "sess-a" && entry.state === "ready",
      ),
    ).toBe(true);
    expect(
      Array.from({ length: sessionStorage.length }, (_, index) =>
        sessionStorage.getItem(sessionStorage.key(index)),
      ).some((value) => value?.includes("session-a.png")),
    ).toBe(true);

    view.rerender(chatElement({ sessionId: "sess-a" }).element);
    await waitFor(() =>
      expect(
        attachmentOutboxMocks.list.mock.calls.filter(([sessionId]) => sessionId === "sess-a")
          .length,
      ).toBeGreaterThan(1),
    );
    expect(await screen.findByText("session-a.png")).toBeInTheDocument();
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
        xhrRequests.some((request) => request.url === "/api/captures/upload"),
      ).toBe(true),
    );

    const uploadCall = xhrRequests.find(
      (request) => request.url === "/api/captures/upload",
    );
    expect(uploadCall).toBeTruthy();
    const formData = uploadCall.body;
    expect(formData.get("source")).toBe("camera");

    await waitFor(() => expect(stopTrack).toHaveBeenCalled());
    expect(await screen.findByLabelText("Vision mode")).toBeInTheDocument();
  });

  it("restores a captured camera attachment after reload and sends its identity", async () => {
    const stopTrack = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: stopTrack }],
    });
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia },
      configurable: true,
    });

    const view = renderChat();
    fireEvent.click(screen.getByRole("button", { name: /open attachments/i }));
    fireEvent.click(await screen.findByRole("button", { name: /capture from camera/i }));
    fireEvent.click(await screen.findByRole("button", { name: "capture" }));

    await waitFor(() =>
      expect(
        Array.from(attachmentOutboxState.entries.values()).some(
          (entry) => entry.state === "ready",
        ),
      ).toBe(true),
    );
    const readyEntry = Array.from(attachmentOutboxState.entries.values()).find(
      (entry) => entry.state === "ready",
    );
    expect(readyEntry.descriptor).toMatchObject({
      remoteUrl: "/api/captures/capture-1/content",
      content_hash: "camera-hash",
      capture_source: "chat_camera",
      capture_id: "capture-1",
      transient: true,
      expires_at: "2026-07-25T12:00:00Z",
    });

    view.unmount();
    sessionStorage.clear();
    renderChat();

    expect(await screen.findByLabelText("Vision mode")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(proxyMocks.chat).toHaveBeenCalled());
    expect(proxyMocks.chat.mock.calls[0][0].attachments[0]).toMatchObject({
      url: expect.stringContaining("/api/captures/capture-1/content"),
      content_hash: "camera-hash",
      capture_source: "chat_camera",
      capture_id: "capture-1",
      transient: true,
      expires_at: "2026-07-25T12:00:00Z",
    });
  });

  it("shows the browser camera permission detail when access is denied", async () => {
    const getUserMedia = vi
      .fn()
      .mockRejectedValue(new Error("Permission denied by browser"));
    Object.defineProperty(navigator, "mediaDevices", {
      value: { getUserMedia },
      configurable: true,
    });

    renderChat();
    fireEvent.click(screen.getByRole("button", { name: /open attachments/i }));
    fireEvent.click(await screen.findByRole("button", { name: /capture from camera/i }));

    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Permission denied by browser",
    );
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
