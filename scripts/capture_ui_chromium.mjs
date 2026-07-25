import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const delay = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

const parseArgs = (values) => {
  const parsed = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index]?.replace(/^--/, "");
    const value = values[index + 1];
    if (!key || typeof value === "undefined") {
      throw new Error(`Invalid argument near ${values[index] || "(end)"}`);
    }
    parsed[key] = value;
  }
  return parsed;
};

const waitForDevToolsPort = async (profilePath, timeoutMs) => {
  const portFile = path.join(profilePath, "DevToolsActivePort");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const [port] = (await readFile(portFile, "utf8")).trim().split(/\r?\n/);
      const numericPort = Number(port);
      if (Number.isInteger(numericPort) && numericPort > 0) return numericPort;
    } catch {
      // Chrome writes the port file after its browser process is ready.
    }
    await delay(50);
  }
  throw new Error("Timed out waiting for Chromium DevTools startup.");
};

const createTarget = async (port, timeoutMs) => {
  const response = await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`,
    { method: "PUT", signal: AbortSignal.timeout(timeoutMs) },
  );
  if (!response.ok) {
    throw new Error(`Could not create Chromium target (${response.status}).`);
  }
  return response.json();
};

const connectCdp = async (webSocketUrl, timeoutMs) => {
  const socket = new WebSocket(webSocketUrl);
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("Timed out connecting to Chromium DevTools.")),
      timeoutMs,
    );
    socket.addEventListener("open", () => {
      clearTimeout(timeout);
      resolve();
    }, { once: true });
    socket.addEventListener("error", () => {
      clearTimeout(timeout);
      reject(new Error("Chromium DevTools websocket failed."));
    }, { once: true });
  });

  let nextId = 1;
  const pending = new Map();
  const eventWaiters = new Map();
  let closedError = null;
  const failPending = (error) => {
    if (closedError) return;
    closedError = error;
    pending.forEach(({ reject }) => reject(error));
    pending.clear();
    eventWaiters.forEach((waiters) => {
      waiters.forEach(({ reject }) => reject(error));
    });
    eventWaiters.clear();
  };
  socket.addEventListener("close", () => {
    failPending(new Error("Chromium DevTools websocket closed."));
  });
  socket.addEventListener("error", () => {
    failPending(new Error("Chromium DevTools websocket failed."));
  });
  socket.addEventListener("message", (event) => {
    let message;
    try {
      message = JSON.parse(String(event.data));
    } catch {
      failPending(new Error("Chromium DevTools returned invalid JSON."));
      return;
    }
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result || {});
      return;
    }
    const waiters = eventWaiters.get(message.method) || [];
    eventWaiters.delete(message.method);
    waiters.forEach(({ resolve }) => resolve(message.params || {}));
  });

  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      if (closedError) {
        reject(closedError);
        return;
      }
      const id = nextId;
      nextId += 1;
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Timed out running Chromium DevTools command ${method}.`));
      }, timeoutMs);
      pending.set(id, {
        resolve: (result) => {
          clearTimeout(timeout);
          resolve(result);
        },
        reject: (error) => {
          clearTimeout(timeout);
          reject(error);
        },
      });
      try {
        socket.send(JSON.stringify({ id, method, params }));
      } catch (error) {
        pending.delete(id);
        clearTimeout(timeout);
        reject(error);
      }
    });

  const waitForEvent = (method, eventTimeoutMs = timeoutMs) =>
    new Promise((resolve, reject) => {
      if (closedError) {
        reject(closedError);
        return;
      }
      const waiters = eventWaiters.get(method) || [];
      const waiter = { resolve: null, reject: null };
      const timeout = setTimeout(() => {
        const active = eventWaiters.get(method) || [];
        eventWaiters.set(method, active.filter((item) => item !== waiter));
        reject(new Error(`Timed out waiting for ${method}.`));
      }, eventTimeoutMs);
      waiter.resolve = (params) => {
        clearTimeout(timeout);
        resolve(params);
      };
      waiter.reject = (error) => {
        clearTimeout(timeout);
        reject(error);
      };
      waiters.push(waiter);
      eventWaiters.set(method, waiters);
    });

  return { send, socket, waitForEvent };
};

const stopChromiumTree = (child) => {
  if (!child?.pid) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    return;
  }
  try {
    child.kill("SIGKILL");
  } catch {
    // The browser may already have exited after the DevTools socket closed.
  }
};

const main = async () => {
  const args = parseArgs(process.argv.slice(2));
  const browserPath = path.resolve(args.browser || "");
  const outputPath = path.resolve(args.output || "");
  const targetUrl = args.url;
  const width = Number(args.width);
  const height = Number(args.height);
  const deviceScaleFactor = Number(args.dpr);
  const timeoutMs = Number(args["timeout-ms"] || 30000);
  const settleMs = Math.min(Number(args["settle-ms"] || 1200), 5000);
  const landscape = width > height;
  if (
    !browserPath ||
    !outputPath ||
    !targetUrl ||
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    !Number.isFinite(deviceScaleFactor)
  ) {
    throw new Error("Missing or invalid Chromium capture arguments.");
  }
  if (typeof WebSocket !== "function") {
    throw new Error(
      "Pixel 9 capture needs a Node.js runtime with global WebSocket support (Node.js 22 or newer is recommended).",
    );
  }

  const profilePath = await mkdtemp(path.join(os.tmpdir(), "float-cdp-profile-"));
  let browserProcess;
  let cdp;
  try {
    browserProcess = spawn(
      browserPath,
      [
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        `--user-data-dir=${profilePath}`,
        "about:blank",
      ],
      { stdio: "ignore", windowsHide: true },
    );
    const port = await waitForDevToolsPort(profilePath, timeoutMs);
    const target = await createTarget(port, timeoutMs);
    cdp = await connectCdp(target.webSocketDebuggerUrl, timeoutMs);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
    ]);
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor,
      mobile: true,
      screenWidth: width,
      screenHeight: height,
      screenOrientation: {
        angle: landscape ? 90 : 0,
        type: landscape ? "landscapePrimary" : "portraitPrimary",
      },
    });
    await cdp.send("Emulation.setTouchEmulationEnabled", {
      enabled: true,
      maxTouchPoints: 5,
    });
    await cdp.send("Network.setUserAgentOverride", {
      userAgent: args.ua,
      platform: "Android",
    });

    const loaded = cdp.waitForEvent("Page.loadEventFired", timeoutMs);
    await cdp.send("Page.navigate", { url: targetUrl });
    await loaded;
    const deadline = Date.now() + timeoutMs;
    let appReady = false;
    while (Date.now() < deadline) {
      const readiness = await cdp.send("Runtime.evaluate", {
        expression:
          "document.readyState === 'complete' && Boolean(document.querySelector('#root')?.firstElementChild)",
        returnByValue: true,
      });
      if (readiness.result?.value === true) {
        appReady = true;
        break;
      }
      await delay(100);
    }
    if (!appReady) {
      throw new Error(`Float did not become ready within ${timeoutMs} ms: ${targetUrl}`);
    }
    await delay(settleMs);

    const metricsResult = await cdp.send("Runtime.evaluate", {
      expression: `JSON.stringify({
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio,
        screen: { width: screen.width, height: screen.height },
        visualViewport: window.visualViewport ? {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          scale: window.visualViewport.scale
        } : null,
        document: {
          clientWidth: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
          clientHeight: document.documentElement.clientHeight,
          scrollHeight: document.documentElement.scrollHeight
        },
        pointerCoarse: matchMedia('(pointer: coarse)').matches,
        hoverNone: matchMedia('(hover: none)').matches,
        orientation: screen.orientation?.type || null,
        userAgent: navigator.userAgent
      })`,
      returnByValue: true,
    });
    const screenshot = await cdp.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    await writeFile(outputPath, Buffer.from(screenshot.data, "base64"));
    process.stdout.write(`${metricsResult.result?.value || "{}"}\n`);
  } finally {
    try {
      cdp?.socket.close();
    } catch {
      // Cleanup continues with the task-owned Chromium process tree.
    }
    stopChromiumTree(browserProcess);
    try {
      await rm(profilePath, {
        recursive: true,
        force: true,
        maxRetries: 10,
        retryDelay: 100,
      });
    } catch {
      // Windows may briefly retain a Chromium profile lock after taskkill.
    }
  }
};

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
