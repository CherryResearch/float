#!/usr/bin/env node
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

const readDevState = () =>
  JSON.parse(fs.readFileSync(path.join(repoRoot, ".dev_state.json"), "utf8"));

const ensureDir = (dir) => fs.mkdirSync(dir, { recursive: true });

const timestamp = () => {
  const date = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
};

const state = readDevState();
const backendPort = Number(process.env.BACKEND_PORT || state.backend_port);
const frontendPort = Number(process.env.FRONTEND_PORT || state.frontend_port);
const backendBase = `http://127.0.0.1:${backendPort}`;
const frontendBase = `http://localhost:${frontendPort}`;
const runId = process.env.RUN_ID || timestamp();
const conversationId =
  process.env.CONVERSATION_ID || `qa/codex-long-windowing-${runId}`;
const compactedConversationId = `${conversationId}-compacted`;
const screenshotDir = path.join(repoRoot, "data", "screenshots");
const logDir = path.join(repoRoot, "logs");
ensureDir(screenshotDir);
ensureDir(logDir);

const screenshotPath = (name) =>
  path.join(screenshotDir, `long-chat-dom-windowing-${runId}-${name}.png`);
const evidencePath = path.join(logDir, `long-chat-dom-windowing-${runId}.json`);

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const fetchJson = async (url, options = {}) => {
  const response = await fetch(url, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }
  if (!response.ok) {
    const error = new Error(`${response.status} ${response.statusText}: ${text}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
};

const buildMessages = (count = 360) =>
  Array.from({ length: count }, (_, index) => {
    const role = index % 2 === 0 ? "user" : "ai";
    const message = {
      id: `synthetic-${index}`,
      role,
      text: `long windowing synthetic message ${index}. ${"context ".repeat(18)}`.trim(),
      timestamp: new Date(Date.UTC(2026, 3, 18, 12, 0, index)).toISOString(),
      metadata: {
        qa_seed: "long-chat-dom-windowing",
        sequence: index,
      },
    };
    if (role === "ai" && index % 10 === 1) {
      message.tools = Array.from({ length: 72 }, (_, toolIndex) => ({
        id: `tool-${index}-${toolIndex}`,
        name: toolIndex % 2 === 0 ? "tool_info" : "read_file",
        args: { index, toolIndex },
        status: "invoked",
        result: {
          ok: true,
          excerpt: `tool result ${toolIndex} for message ${index}`,
        },
      }));
      message.metadata.inline_tool_payloads = message.tools.map((tool) => ({
        name: tool.name,
        result: tool.result,
      }));
    }
    return message;
  });

const countServerMessages = async (id) => {
  const payload = await fetchJson(
    `${backendBase}/api/conversations/${encodeURIComponent(id)}`,
  );
  return Array.isArray(payload?.messages) ? payload.messages.length : 0;
};

const getFreePort = () =>
  new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });

const resolveBrowserPath = () => {
  const candidates = [
    process.env.CHROME_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    path.join(os.homedir(), "AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"),
    path.join(os.homedir(), "AppData\\Local\\Microsoft\\Edge\\Application\\msedge.exe"),
  ].filter(Boolean);
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) {
    throw new Error("No Chrome or Edge executable found. Set CHROME_PATH to continue.");
  }
  return found;
};

const httpGetJson = (url) =>
  new Promise((resolve, reject) => {
    const request = http.get(url, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("error", reject);
    request.setTimeout(3000, () => {
      request.destroy(new Error(`Timed out fetching ${url}`));
    });
  });

const waitForJson = async (url, timeoutMs = 15000) => {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      return await httpGetJson(url);
    } catch (error) {
      lastError = error;
      await delay(250);
    }
  }
  throw lastError || new Error(`Timed out waiting for ${url}`);
};

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    this.opened = new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.id && this.pending.has(payload.id)) {
        const { resolve, reject } = this.pending.get(payload.id);
        this.pending.delete(payload.id);
        if (payload.error) {
          reject(new Error(payload.error.message || JSON.stringify(payload.error)));
        } else {
          resolve(payload.result || {});
        }
        return;
      }
      if (payload.method && this.events.has(payload.method)) {
        for (const handler of this.events.get(payload.method)) {
          handler(payload.params || {});
        }
      }
    });
  }

  async send(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const message = JSON.stringify({ id, method, params });
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`CDP command timed out: ${method}`));
      }, 20000);
    });
    this.ws.send(message);
    return result;
  }

  once(method) {
    return new Promise((resolve) => {
      const handler = (params) => {
        const handlers = this.events.get(method) || [];
        this.events.set(
          method,
          handlers.filter((item) => item !== handler),
        );
        resolve(params);
      };
      const handlers = this.events.get(method) || [];
      handlers.push(handler);
      this.events.set(method, handlers);
    });
  }

  close() {
    this.ws.close();
  }
}

const startBrowser = async () => {
  const browserPath = resolveBrowserPath();
  const remotePort = await getFreePort();
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "float-long-chat-"));
  const args = [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${remotePort}`,
    `--user-data-dir=${profileDir}`,
    "--window-size=1440,900",
    "--js-flags=--expose-gc",
    "about:blank",
  ];
  const proc = spawn(browserPath, args, { stdio: ["ignore", "pipe", "pipe"] });
  const stderr = [];
  proc.stderr.on("data", (chunk) => stderr.push(String(chunk)));
  const version = await waitForJson(`http://127.0.0.1:${remotePort}/json/version`);
  const tabs = await waitForJson(`http://127.0.0.1:${remotePort}/json/list`);
  const pageTarget = tabs.find((target) => target.type === "page") || tabs[0];
  if (!pageTarget?.webSocketDebuggerUrl) {
    throw new Error("Could not find a debuggable page target.");
  }
  const page = new CdpClient(pageTarget.webSocketDebuggerUrl);
  await page.opened;
  await page.send("Page.enable");
  await page.send("Runtime.enable");
  await page.send("Performance.enable").catch(() => {});
  await page.send("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });
  return {
    browserPath,
    version,
    page,
    close: async () => {
      page.close();
      proc.kill();
      await delay(250);
      fs.rmSync(profileDir, { recursive: true, force: true });
    },
    stderr: () => stderr.join(""),
  };
};

const evaluate = async (page, fn, arg) => {
  const source = arg === undefined
    ? `(${fn})()`
    : `(${fn})(${JSON.stringify(arg)})`;
  const result = await page.send("Runtime.evaluate", {
    expression: source,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails));
  }
  return result.result?.value;
};

const navigate = async (page, url) => {
  const loaded = page.once("Page.loadEventFired");
  await page.send("Page.navigate", { url });
  await loaded;
};

const reload = async (page) => {
  const loaded = page.once("Page.loadEventFired");
  await page.send("Page.reload", { ignoreCache: true });
  await loaded;
};

const waitForExpression = async (page, fn, timeoutMs = 30000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (await evaluate(page, fn)) return;
    } catch {
      // Keep polling through reload/transient script errors.
    }
    await delay(250);
  }
  throw new Error("Timed out waiting for browser condition.");
};

const captureScreenshot = async (page, filePath) => {
  const shot = await page.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
  });
  fs.writeFileSync(filePath, Buffer.from(shot.data, "base64"));
};

const getPerformanceMetric = (metrics, name) => {
  const item = metrics.find((entry) => entry.name === name);
  return item ? item.value : null;
};

const collectProbe = async (page, label) => {
  await evaluate(page, () => {
    if (typeof window.gc === "function") window.gc();
  }).catch(() => {});
  const domCounters = await page.send("Memory.getDOMCounters").catch(() => null);
  const performanceMetrics = await page.send("Performance.getMetrics").catch(() => null);
  const pageProbe = await evaluate(page, () => {
    const readStorage = (key) => {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    };
    const parseArray = (value) => {
      try {
        const parsed = JSON.parse(value || "[]");
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    };
    const parseObject = (value) => {
      try {
        const parsed = JSON.parse(value || "null");
        return parsed && typeof parsed === "object" ? parsed : null;
      } catch {
        return null;
      }
    };
    const storedConversation = parseArray(readStorage("conversation"));
    const trimMeta = parseObject(readStorage("float:conversation-window"));
    const chatBox = document.querySelector(".chat-box");
    const compactionNotice = document.querySelector(".conversation-compaction-notice");
    const compactionNoticeRect = compactionNotice
      ? (() => {
          const rect = compactionNotice.getBoundingClientRect();
          const topElement = document.elementFromPoint(
            rect.left + rect.width / 2,
            rect.top + rect.height / 2,
          );
          return {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            elementAtCenter: topElement
              ? {
                  tagName: topElement.tagName,
                  className: String(topElement.className || ""),
                }
              : null,
          };
        })()
      : null;
    const messageNodes = document.querySelectorAll(".chat-box .user-msg, .chat-box .ai-msg");
    const storedToolCounts = storedConversation.map((message) =>
      Array.isArray(message?.tools) ? message.tools.length : 0,
    );
    const text = document.body.innerText || "";
    return {
      renderedMessageNodes: messageNodes.length,
      renderedUserMessages: document.querySelectorAll(".chat-box .user-msg").length,
      renderedAiMessages: document.querySelectorAll(".chat-box .ai-msg").length,
      renderedToolNodes: document.querySelectorAll(".chat-box .tool-call-card, .chat-box .tool-payload").length,
      storedConversationCount: storedConversation.length,
      maxStoredToolCount: storedToolCounts.length ? Math.max(...storedToolCounts) : 0,
      trimMeta,
      containsFirstSeedMessage: text.includes("synthetic message 0."),
      containsLastSeedMessage: text.includes("synthetic message 359."),
      compactionBannerText: compactionNotice?.innerText || "",
      compactionBannerRect: compactionNoticeRect,
      scrollHeight: chatBox ? chatBox.scrollHeight : null,
      clientHeight: chatBox ? chatBox.clientHeight : null,
      location: window.location.href,
      jsHeap: performance?.memory
        ? {
            usedJSHeapSize: performance.memory.usedJSHeapSize,
            totalJSHeapSize: performance.memory.totalJSHeapSize,
            jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
          }
        : null,
    };
  });
  const metrics = performanceMetrics?.metrics || [];
  return {
    label,
    collectedAt: new Date().toISOString(),
    domCounters,
    performance: {
      JSHeapUsedSize: getPerformanceMetric(metrics, "JSHeapUsedSize"),
      JSHeapTotalSize: getPerformanceMetric(metrics, "JSHeapTotalSize"),
      Nodes: getPerformanceMetric(metrics, "Nodes"),
      LayoutCount: getPerformanceMetric(metrics, "LayoutCount"),
      RecalcStyleCount: getPerformanceMetric(metrics, "RecalcStyleCount"),
    },
    page: pageProbe,
  };
};

const main = async () => {
  const messages = buildMessages();
  const health = await fetchJson(`${backendBase}/health`, { headers: {} });
  await fetchJson(`${backendBase}/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: "POST",
    body: JSON.stringify({
      name: "Codex long-chat DOM windowing QA",
      messages,
    }),
  });
  const serverSeededCount = await countServerMessages(conversationId);

  const partialTail = messages.slice(-80);
  const partialClientWindow = {
    truncated: true,
    source: "qa-script",
    total_messages: messages.length,
    omitted_messages: messages.length - partialTail.length,
    start_index: messages.length - partialTail.length,
    message_limit: partialTail.length,
  };
  const partialSave = await fetchJson(
    `${backendBase}/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        name: "Codex long-chat DOM windowing QA",
        messages: partialTail,
        client_window: partialClientWindow,
      }),
    },
  );
  const countAfterPartialSave = await countServerMessages(conversationId);

  const appendedMessages = [
    ...partialTail,
    {
      id: "synthetic-360",
      role: "user",
      text: "new message after a client-window save",
    },
    {
      id: "synthetic-361",
      role: "ai",
      text: "new assistant shell after a client-window save",
      metadata: { status: "complete" },
    },
  ];
  const partialAppendSave = await fetchJson(
    `${backendBase}/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        name: "Codex long-chat DOM windowing QA",
        messages: appendedMessages,
        client_window: {
          ...partialClientWindow,
          total_messages: messages.length + 2,
        },
      }),
    },
  );
  const countAfterPartialAppend = await countServerMessages(conversationId);

  await fetchJson(`${backendBase}/api/tools/register`, {
    method: "POST",
    body: JSON.stringify({ name: "compact_conversation_preview" }),
  }).catch(() => null);
  await fetchJson(`${backendBase}/api/tools/register`, {
    method: "POST",
    body: JSON.stringify({ name: "compact_conversation_write" }),
  }).catch(() => null);
  const compactionPreview = await fetchJson(`${backendBase}/api/tools/invoke`, {
    method: "POST",
    body: JSON.stringify({
      name: "compact_conversation_preview",
      args: {
        conversation_id: conversationId,
        keep_last: 24,
        max_summary_chars: 2500,
        summary_mode: "deterministic",
      },
      session_id: conversationId,
      message_id: "qa-compaction-preview",
    }),
  });
  const compactionWrite = await fetchJson(`${backendBase}/api/tools/invoke`, {
    method: "POST",
    body: JSON.stringify({
      name: "compact_conversation_write",
      args: {
        conversation_id: conversationId,
        keep_last: 24,
        max_summary_chars: 2500,
        summary_mode: "deterministic",
        target_conversation_id: compactedConversationId,
        replace: false,
      },
      session_id: conversationId,
      message_id: "qa-compaction-write",
    }),
  });
  const compactedPayload = await fetchJson(
    `${backendBase}/api/conversations/${encodeURIComponent(compactedConversationId)}`,
  );
  const compactedMessages = Array.isArray(compactedPayload?.messages)
    ? compactedPayload.messages
    : [];

  const browser = await startBrowser();
  const { page } = browser;
  const probes = [];
  try {
    const seedScript = await page.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `
        (() => {
          const seedMessages = ${JSON.stringify(messages)};
          localStorage.clear();
          sessionStorage.clear();
          localStorage.setItem("conversation", JSON.stringify(seedMessages));
          localStorage.setItem("sessionId", ${JSON.stringify(conversationId)});
          localStorage.setItem("sessionName", "Codex long-chat DOM windowing QA");
          localStorage.setItem("backendMode", "api");
          localStorage.setItem("floatConversationMessageLimit", "80");
          localStorage.setItem("floatConversationToolLimit", "40");
        })();
      `,
    });

    await navigate(page, "about:blank");
    probes.push(await collectProbe(page, "blank_baseline"));
    await navigate(page, frontendBase);
    await waitForExpression(page, () => Boolean(document.querySelector(".chat-box")));
    await waitForExpression(page, () => {
      try {
        const meta = JSON.parse(localStorage.getItem("float:conversation-window") || "null");
        return Boolean(meta?.truncated && meta.total_messages >= 360);
      } catch {
        return false;
      }
    });
    probes.push(await collectProbe(page, "after_full_localstorage_load"));
    await captureScreenshot(page, screenshotPath("after-load"));

    await evaluate(page, () => {
      const chatBox = document.querySelector(".chat-box");
      if (chatBox) chatBox.scrollTop = 0;
    });
    await delay(350);
    probes.push(await collectProbe(page, "after_scroll_top"));
    await captureScreenshot(page, screenshotPath("after-scroll-top"));

    await reload(page);
    await waitForExpression(page, () => Boolean(document.querySelector(".chat-box")));
    probes.push(await collectProbe(page, "after_reload"));
    await captureScreenshot(page, screenshotPath("after-reload"));

    if (seedScript?.identifier) {
      await page
        .send("Page.removeScriptToEvaluateOnNewDocument", {
          identifier: seedScript.identifier,
        })
        .catch(() => {});
    }
    await evaluate(
      page,
      ({ compacted, compactedId }) => {
        localStorage.setItem("conversation", JSON.stringify(compacted));
        localStorage.setItem("sessionId", compactedId);
        localStorage.setItem("sessionName", "Codex compacted long-chat QA");
        localStorage.removeItem("float:conversation-window");
      },
      { compacted: compactedMessages, compactedId: compactedConversationId },
    );
    await reload(page);
    await waitForExpression(page, () => Boolean(document.querySelector(".chat-box")));
    probes.push(await collectProbe(page, "after_compacted_load"));
    await captureScreenshot(page, screenshotPath("after-compaction"));
  } finally {
    await browser.close();
  }

  const evidence = {
    runId,
    frontendBase,
    backendBase,
    browserPath: browser.browserPath,
    browserVersion: browser.version,
    health,
    conversationId,
    compactedConversationId,
    seededMessages: messages.length,
    serverSeededCount,
    partialSave,
    countAfterPartialSave,
    partialAppendSave,
    countAfterPartialAppend,
    compactionPreview: compactionPreview?.result?.data || compactionPreview,
    compactionWrite: compactionWrite?.result?.data || compactionWrite,
    compactedMessageCount: compactedMessages.length,
    probes,
    screenshots: {
      afterLoad: screenshotPath("after-load"),
      afterScrollTop: screenshotPath("after-scroll-top"),
      afterReload: screenshotPath("after-reload"),
      afterCompaction: screenshotPath("after-compaction"),
    },
    limits: {
      expectedRenderedMessageLimit: 80,
      expectedToolPayloadLimit: 40,
      proxy: "Chrome DevTools Memory.getDOMCounters + Performance.getMetrics + rendered .chat-box message node counts",
    },
  };
  fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify({
    evidencePath,
    screenshots: evidence.screenshots,
    conversationId,
    compactedConversationId,
    serverSeededCount,
    countAfterPartialSave,
    countAfterPartialAppend,
    renderedAfterLoad: probes.find((probe) => probe.label === "after_full_localstorage_load")?.page,
  }, null, 2));
};

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
