import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  cleanupExpiredAttachmentOutboxEntries,
  deleteAttachmentOutboxEntry,
  deleteSentAttachmentOutboxEntries,
  listAttachmentOutboxEntries,
  putAttachmentOutboxEntry,
} from "../attachmentOutbox";

const cloneForStorage = (value) => {
  if (value instanceof File) {
    return new File([value], value.name, {
      type: value.type,
      lastModified: value.lastModified,
    });
  }
  if (value instanceof Blob) {
    return new Blob([value], { type: value.type });
  }
  if (Array.isArray(value)) return value.map(cloneForStorage);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneForStorage(item)]),
    );
  }
  return value;
};

class FakeTransaction {
  constructor(store) {
    this.store = store;
    this.pending = 0;
    this.completed = false;
  }

  objectStore() {
    return this.store.bindTransaction(this);
  }

  request(action) {
    this.pending += 1;
    const request = {};
    queueMicrotask(() => {
      try {
        request.result = action();
        request.onsuccess?.();
      } catch (error) {
        request.error = error;
        request.onerror?.();
        this.onerror?.();
      } finally {
        this.pending -= 1;
        this.scheduleCompletion();
      }
    });
    return request;
  }

  scheduleCompletion() {
    queueMicrotask(() => {
      if (!this.completed && this.pending === 0) {
        this.completed = true;
        this.oncomplete?.();
      }
    });
  }
}

class FakeObjectStore {
  constructor() {
    this.records = new Map();
    this.indexNames = { contains: (name) => name === "sessionId" };
  }

  bindTransaction(transaction) {
    return {
      get: (key) => transaction.request(() => cloneForStorage(this.records.get(JSON.stringify(key)))),
      getAll: () => transaction.request(() =>
        Array.from(this.records.values(), cloneForStorage),
      ),
      put: (entry) => transaction.request(() => {
        this.records.set(
          JSON.stringify([entry.sessionId, entry.id]),
          cloneForStorage(entry),
        );
        return [entry.sessionId, entry.id];
      }),
      delete: (key) => transaction.request(() => this.records.delete(JSON.stringify(key))),
      index: () => ({
        getAll: (sessionId) => transaction.request(() =>
          Array.from(this.records.values())
            .filter((entry) => entry.sessionId === sessionId)
            .map(cloneForStorage),
        ),
      }),
    };
  }

  createIndex() {}
}

const createFakeIndexedDb = () => {
  let store = null;
  const database = {
    objectStoreNames: { contains: () => Boolean(store) },
    createObjectStore: () => {
      store = new FakeObjectStore();
      return store;
    },
    transaction: () => new FakeTransaction(store),
    close: () => {},
  };

  return {
    open: () => {
      const request = {};
      queueMicrotask(() => {
        request.result = database;
        if (!store) request.onupgradeneeded?.();
        request.onsuccess?.();
      });
      return request;
    },
  };
};

const setIndexedDb = (value) => {
  Object.defineProperty(globalThis, "indexedDB", {
    configurable: true,
    writable: true,
    value,
  });
};

describe("attachment outbox", () => {
  beforeEach(() => {
    setIndexedDb(createFakeIndexedDb());
  });

  afterEach(() => {
    delete globalThis.indexedDB;
  });

  it("stores structured-cloned files and lists only the requested session", async () => {
    const file = new File(["pixel-nine"], "photo.png", { type: "image/png" });

    await putAttachmentOutboxEntry("session-a", {
      id: "attachment-1",
      file,
      name: file.name,
      type: file.type,
      size: file.size,
      origin: "captured",
      captureSource: "chat_camera",
      state: "uploading",
      descriptor: { previewUrl: "blob:preview" },
    }, { now: 100, ttlMs: 500 });
    await putAttachmentOutboxEntry("session-b", {
      id: "attachment-1",
      file: new Blob(["other"], { type: "text/plain" }),
      state: "uploading",
    }, { now: 110, ttlMs: 500 });

    const entries = await listAttachmentOutboxEntries("session-a", { now: 120 });

    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      sessionId: "session-a",
      id: "attachment-1",
      name: "photo.png",
      type: "image/png",
      size: file.size,
      origin: "captured",
      captureSource: "chat_camera",
      state: "uploading",
      createdAt: 100,
      updatedAt: 100,
      expiresAt: 600,
    });
    expect(entries[0].file).toBeInstanceOf(File);
    expect(entries[0].file).not.toBe(file);
    expect(entries[0].file).toMatchObject({
      name: "photo.png",
      type: "image/png",
      size: file.size,
    });
  });

  it("updates one composite key while preserving its creation time", async () => {
    await putAttachmentOutboxEntry("session-a", {
      id: "attachment-1",
      name: "draft.txt",
      state: "uploading",
    }, { now: 100, ttlMs: 50 });

    const updated = await putAttachmentOutboxEntry("session-a", {
      id: "attachment-1",
      state: "uploaded",
      descriptor: { remoteUrl: "/api/attachments/hash/draft.txt" },
    }, { now: 125, ttlMs: 100 });

    expect(updated).toMatchObject({
      name: "draft.txt",
      state: "uploaded",
      createdAt: 100,
      updatedAt: 125,
      expiresAt: 225,
    });
    expect(await listAttachmentOutboxEntries("session-a", { now: 130 })).toEqual([
      expect.objectContaining({ id: "attachment-1", state: "uploaded" }),
    ]);
  });

  it("deletes one entry or an explicit successful-send batch", async () => {
    for (const [id, state] of [["one", "uploaded"], ["two", "uploaded"], ["three", "sent"]]) {
      await putAttachmentOutboxEntry("session-a", { id, state }, { now: 100 });
    }

    expect(await deleteAttachmentOutboxEntry("session-a", "one")).toBe(true);
    expect(await deleteAttachmentOutboxEntry("session-a", "missing")).toBe(false);
    expect(await deleteSentAttachmentOutboxEntries("session-a", ["two"])).toBe(1);
    expect(await listAttachmentOutboxEntries("session-a", { now: 101 })).toEqual([
      expect.objectContaining({ id: "three", state: "sent" }),
    ]);
    expect(await deleteSentAttachmentOutboxEntries("session-a")).toBe(1);
    expect(await listAttachmentOutboxEntries("session-a", { now: 101 })).toEqual([]);
  });

  it("prunes expired entries while retaining live entries", async () => {
    await putAttachmentOutboxEntry("session-a", { id: "expired" }, {
      now: 100,
      ttlMs: 10,
    });
    await putAttachmentOutboxEntry("session-a", { id: "live" }, {
      now: 100,
      ttlMs: 100,
    });
    await putAttachmentOutboxEntry("session-b", { id: "expired" }, {
      now: 50,
      ttlMs: 10,
    });

    expect(await listAttachmentOutboxEntries("session-a", { now: 150 })).toEqual([
      expect.objectContaining({ id: "live" }),
    ]);
    expect(await cleanupExpiredAttachmentOutboxEntries({ now: 150 })).toBe(1);
    expect(await listAttachmentOutboxEntries("session-b", {
      now: 150,
      pruneExpired: false,
    })).toEqual([]);
  });

  it("degrades to safe results when IndexedDB is unavailable", async () => {
    delete globalThis.indexedDB;

    await expect(putAttachmentOutboxEntry("session-a", { id: "one" })).resolves.toBeNull();
    await expect(listAttachmentOutboxEntries("session-a")).resolves.toEqual([]);
    await expect(deleteAttachmentOutboxEntry("session-a", "one")).resolves.toBe(false);
    await expect(deleteSentAttachmentOutboxEntries("session-a", ["one"])).resolves.toBe(0);
    await expect(cleanupExpiredAttachmentOutboxEntries()).resolves.toBe(0);
  });
});
