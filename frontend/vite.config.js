import { defineConfig, createLogger } from "vite";
import react from "@vitejs/plugin-react";

// The launcher supplies a connectable address for the selected bind. In the
// normal listener-toggle flow this remains IPv4 loopback for both 127.0.0.1
// and 0.0.0.0, avoiding Windows localhost/IPv6 ambiguity.
const rawBackendProxyHost = (process.env.BACKEND_PROXY_HOST || '127.0.0.1').trim();
const backendProxyHost = rawBackendProxyHost.includes(':') && !rawBackendProxyHost.startsWith('[')
  ? `[${rawBackendProxyHost}]`
  : rawBackendProxyHost;
const backendTarget = `http://${backendProxyHost}:${process.env.BACKEND_PORT || 8000}`;
const configuredHost = process.env.VITE_HOST?.trim();
const configuredAllowedHosts = (process.env.VITE_ALLOWED_HOSTS || '')
  .split(',')
  .map((host) => host.trim())
  .filter(Boolean);
const allowedHosts = Array.from(new Set(['.ts.net', ...configuredAllowedHosts]));
const backendProxyToken = (process.env.BACKEND_PROXY_TOKEN || '').trim();
const trustedBackendProxyHeaders = {
  'x-float-frontend-proxy': '1',
  ...(backendProxyToken
    ? { 'x-float-frontend-proxy-token': backendProxyToken }
    : {}),
};
const connectionErrorCodes = new Set(['ECONNREFUSED', 'ECONNRESET']);
const STARTUP_COLOR = '\x1b[33m';
const COLOR_RESET = '\x1b[0m';
const STARTUP_COOLDOWN_MS = 1200;
const baseLogger = createLogger();
let lastStartupNoticeAt = 0;

const extractProxyPath = (message) => {
  if (typeof message !== 'string') {
    return '';
  }
  const match = message.match(/http proxy error:\s*([^\s]+)/i);
  return match?.[1]?.trim() || '';
};

const isTransientProxyError = (message, error) => {
  if (typeof message !== 'string' || !message.includes('http proxy error')) {
    return false;
  }
  if (!error) {
    return true;
  }
  if (connectionErrorCodes.has(error.code)) {
    return true;
  }
  return /ECONNREFUSED|ECONNRESET/.test(error.message || '');
};

const formatStartupNotice = (path) => {
  const suffix = path ? ` – waiting on ${path}` : '';
  return `${STARTUP_COLOR}backend starting up${suffix}${COLOR_RESET}`;
};

const customLogger = {
  ...baseLogger,
  error(message, options = {}) {
    if (isTransientProxyError(message, options.error)) {
      const now = Date.now();
      if (now - lastStartupNoticeAt > STARTUP_COOLDOWN_MS) {
        lastStartupNoticeAt = now;
        const proxyPath = extractProxyPath(message);
        baseLogger.warn(formatStartupNotice(proxyPath));
      }
      return;
    }
    baseLogger.error(message, options);
  },
};

const attachProxyHandlers = (proxy) => {
  proxy.on('error', (err, req, res) => {
    if (!err || (err.code !== 'ECONNREFUSED' && err.code !== 'ECONNRESET')) {
      return;
    }
    if (res && typeof res.writeHead === 'function' && !res.headersSent) {
      res.writeHead(502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'backend unavailable' }));
    }
  });
};

export default defineConfig({
  customLogger,
  plugins: [react()],
  server: {
    ...(configuredHost ? { host: configuredHost } : {}),
    ...(allowedHosts.length ? { allowedHosts } : {}),
    // Use VITE_PORT env var or default to 5173
    port: parseInt(process.env.VITE_PORT, 10) || 5173,
    proxy: {
      "/api": {
        // Proxy to the backend port set via BACKEND_PORT env var
        target: backendTarget,
        changeOrigin: true,
        secure: false,
        headers: trustedBackendProxyHeaders,
        ws: true,
        configure: attachProxyHandlers,
      },
      "/health": {
        // Ensure backend health checks resolve during dev proxying
        target: backendTarget,
        changeOrigin: true,
        secure: false,
        headers: trustedBackendProxyHeaders,
        configure: attachProxyHandlers,
      },
      "/conversations": {
        // Proxy conversation endpoints to backend
        target: backendTarget,
        changeOrigin: true,
        secure: false,
        headers: trustedBackendProxyHeaders,
        configure: attachProxyHandlers,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
