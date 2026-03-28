import axios from "axios";

const API_BASE = 
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_BASE_URL) || 
  "/api";

const DEVICE_ID_KEY = "device_id";
const DEVICE_TOKEN_KEY = "device_token";

export const getStoredDevice = () => ({
  id: localStorage.getItem(DEVICE_ID_KEY) || null,
  token: localStorage.getItem(DEVICE_TOKEN_KEY) || null,
});

export const registerDevice = async (publicKey, name = undefined, capabilities = undefined) => {
  const res = await axios.post(`${API_BASE}/devices/register`, {
    public_key: publicKey,
    name,
    capabilities,
  });
  const id = res.data?.device?.id;
  if (id) {
    localStorage.setItem(DEVICE_ID_KEY, id);
  }
  return id;
};

export const issueToken = async (deviceId, scopes = ["sync", "stream"], ttlSeconds = 3600) => {
  const res = await axios.post(`${API_BASE}/devices/token`, {
    device_id: deviceId,
    scopes,
    ttl_seconds: ttlSeconds,
  });
  const token = res.data?.token;
  if (token) {
    localStorage.setItem(DEVICE_TOKEN_KEY, token);
  }
  return token;
};

export const ensureDeviceAndToken = async () => {
  let { id, token } = getStoredDevice();
  if (!id) {
    // For Phase 1, generate a simple ephemeral "public key"
    const pub = crypto.randomUUID();
    id = await registerDevice(pub, navigator.userAgent.slice(0, 60));
  }
  if (!token) {
    token = await issueToken(id);
  }
  return { id, token };
};

const authHeaders = () => {
  const token = localStorage.getItem(DEVICE_TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export const getCursor = async () => {
  const res = await axios.get(`${API_BASE}/sync/cursor`, { headers: authHeaders() });
  return res.data;
};

export const getChanges = async (cursor = "0") => {
  const res = await axios.post(
    `${API_BASE}/sync/changes`,
    { cursor },
    { headers: { ...authHeaders(), "Content-Type": "application/json" } },
  );
  return res.data;
};

export const uploadContent = async (content) => {
  const res = await axios.post(
    `${API_BASE}/sync/upload`,
    { content },
    { headers: { ...authHeaders(), "Content-Type": "application/json" } },
  );
  return res.data;
};

export const downloadContent = async (contentHash) => {
  const res = await axios.get(`${API_BASE}/sync/download/${contentHash}`, { headers: authHeaders() });
  return res.data;
};


