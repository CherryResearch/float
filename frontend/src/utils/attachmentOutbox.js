const ATTACHMENT_OUTBOX_DB_NAME = "float-attachment-outbox";
const ATTACHMENT_OUTBOX_DB_VERSION = 1;
const ATTACHMENT_OUTBOX_STORE_NAME = "attachments";
const ATTACHMENT_OUTBOX_SESSION_INDEX = "sessionId";

export const ATTACHMENT_OUTBOX_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const getIndexedDbFactory = () => {
  try {
    return globalThis.indexedDB || null;
  } catch {
    return null;
  }
};

const normalizeKey = (value) => String(value ?? "").trim();

const normalizeTimestamp = (value, fallback = Date.now()) => {
  const timestamp = Number(value);
  return Number.isFinite(timestamp) ? timestamp : fallback;
};

const normalizeTtl = (value) => {
  const ttl = Number(value);
  return Number.isFinite(ttl) && ttl > 0 ? ttl : ATTACHMENT_OUTBOX_TTL_MS;
};

const isExpired = (entry, now) => {
  const expiresAt = Number(entry?.expiresAt);
  return Number.isFinite(expiresAt) && expiresAt <= now;
};

const openAttachmentOutbox = () => {
  const indexedDb = getIndexedDbFactory();
  if (!indexedDb || typeof indexedDb.open !== "function") {
    return Promise.resolve(null);
  }

  return new Promise((resolve) => {
    let request;
    try {
      request = indexedDb.open(
        ATTACHMENT_OUTBOX_DB_NAME,
        ATTACHMENT_OUTBOX_DB_VERSION,
      );
    } catch {
      resolve(null);
      return;
    }

    request.onupgradeneeded = () => {
      const database = request.result;
      let store;
      if (!database.objectStoreNames.contains(ATTACHMENT_OUTBOX_STORE_NAME)) {
        store = database.createObjectStore(ATTACHMENT_OUTBOX_STORE_NAME, {
          keyPath: ["sessionId", "id"],
        });
      } else {
        store = request.transaction.objectStore(ATTACHMENT_OUTBOX_STORE_NAME);
      }
      if (!store.indexNames.contains(ATTACHMENT_OUTBOX_SESSION_INDEX)) {
        store.createIndex(
          ATTACHMENT_OUTBOX_SESSION_INDEX,
          ATTACHMENT_OUTBOX_SESSION_INDEX,
          { unique: false },
        );
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
};

const runTransaction = async (mode, execute, fallback) => {
  const database = await openAttachmentOutbox();
  if (!database) return fallback;

  return new Promise((resolve) => {
    let settled = false;
    let result = fallback;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      try {
        database.close();
      } catch {
        // Closing an already-closed database is harmless.
      }
      resolve(value);
    };

    try {
      const transaction = database.transaction(
        ATTACHMENT_OUTBOX_STORE_NAME,
        mode,
      );
      const store = transaction.objectStore(ATTACHMENT_OUTBOX_STORE_NAME);
      transaction.oncomplete = () => finish(result);
      transaction.onerror = () => finish(fallback);
      transaction.onabort = () => finish(fallback);
      execute(store, (value) => {
        result = value;
      });
    } catch {
      finish(fallback);
    }
  });
};

export const putAttachmentOutboxEntry = (
  sessionId,
  entry,
  { now = Date.now(), ttlMs = ATTACHMENT_OUTBOX_TTL_MS } = {},
) => {
  const normalizedSessionId = normalizeKey(sessionId);
  const normalizedId = normalizeKey(entry?.id);
  if (!normalizedSessionId || !normalizedId || !entry || typeof entry !== "object") {
    return Promise.resolve(null);
  }

  const updatedAt = normalizeTimestamp(now);
  const expiresAt = updatedAt + normalizeTtl(ttlMs);
  return runTransaction(
    "readwrite",
    (store, setResult) => {
      const request = store.get([normalizedSessionId, normalizedId]);
      request.onsuccess = () => {
        const existing = request.result;
        const storedEntry = {
          ...(existing && typeof existing === "object" ? existing : {}),
          ...entry,
          sessionId: normalizedSessionId,
          id: normalizedId,
          createdAt: normalizeTimestamp(existing?.createdAt ?? entry.createdAt, updatedAt),
          updatedAt,
          expiresAt,
        };
        store.put(storedEntry);
        setResult(storedEntry);
      };
    },
    null,
  );
};

export const listAttachmentOutboxEntries = (
  sessionId,
  { now = Date.now(), pruneExpired = true } = {},
) => {
  const normalizedSessionId = normalizeKey(sessionId);
  if (!normalizedSessionId) return Promise.resolve([]);

  const currentTime = normalizeTimestamp(now);
  return runTransaction(
    pruneExpired ? "readwrite" : "readonly",
    (store, setResult) => {
      const request = store.index(ATTACHMENT_OUTBOX_SESSION_INDEX).getAll(
        normalizedSessionId,
      );
      request.onsuccess = () => {
        const activeEntries = [];
        for (const entry of request.result || []) {
          if (pruneExpired && isExpired(entry, currentTime)) {
            store.delete([entry.sessionId, entry.id]);
          } else {
            activeEntries.push(entry);
          }
        }
        activeEntries.sort(
          (left, right) => Number(left.updatedAt || 0) - Number(right.updatedAt || 0),
        );
        setResult(activeEntries);
      };
    },
    [],
  );
};

export const deleteAttachmentOutboxEntry = (sessionId, attachmentId) => {
  const normalizedSessionId = normalizeKey(sessionId);
  const normalizedId = normalizeKey(attachmentId);
  if (!normalizedSessionId || !normalizedId) return Promise.resolve(false);

  return runTransaction(
    "readwrite",
    (store, setResult) => {
      const request = store.get([normalizedSessionId, normalizedId]);
      request.onsuccess = () => {
        if (!request.result) {
          setResult(false);
          return;
        }
        store.delete([normalizedSessionId, normalizedId]);
        setResult(true);
      };
    },
    false,
  );
};

export const deleteSentAttachmentOutboxEntries = (
  sessionId,
  attachmentIds = null,
) => {
  const normalizedSessionId = normalizeKey(sessionId);
  if (!normalizedSessionId) return Promise.resolve(0);

  const requestedIds = Array.isArray(attachmentIds)
    ? new Set(attachmentIds.map(normalizeKey).filter(Boolean))
    : null;
  return runTransaction(
    "readwrite",
    (store, setResult) => {
      const request = store.index(ATTACHMENT_OUTBOX_SESSION_INDEX).getAll(
        normalizedSessionId,
      );
      request.onsuccess = () => {
        const entriesToDelete = (request.result || []).filter((entry) =>
          requestedIds ? requestedIds.has(entry.id) : entry.state === "sent",
        );
        for (const entry of entriesToDelete) {
          store.delete([entry.sessionId, entry.id]);
        }
        setResult(entriesToDelete.length);
      };
    },
    0,
  );
};

export const cleanupExpiredAttachmentOutboxEntries = ({ now = Date.now() } = {}) => {
  const currentTime = normalizeTimestamp(now);
  return runTransaction(
    "readwrite",
    (store, setResult) => {
      const request = store.getAll();
      request.onsuccess = () => {
        const expiredEntries = (request.result || []).filter((entry) =>
          isExpired(entry, currentTime),
        );
        for (const entry of expiredEntries) {
          store.delete([entry.sessionId, entry.id]);
        }
        setResult(expiredEntries.length);
      };
    },
    0,
  );
};
