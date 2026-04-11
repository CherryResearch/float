import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import "../styles/KnowledgeViewer.css";
import FilterBar from "./FilterBar";
import {
  buildGraphContext,
  buildMemorySearchText,
  getMemoryFilterTimestamp,
  normalizeDateBoundary,
  serializeMemoryValue,
} from "../utils/memoryPanel";

const toLocal = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "-");
const toLocalDate = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString() : "-");

const SENSITIVITY_OPTIONS = [
  "mundane",
  "public",
  "personal",
  "protected",
  "secret",
];

const MemoryEditor = ({ item, onClose, onSave }) => {
  const [keyText, setKeyText] = useState(item?.key || "");
  const [valueText, setValueText] = useState(
    item && typeof item.value !== "undefined"
      ? JSON.stringify(item.value, null, 2)
      : "{}",
  );
  const [importance, setImportance] = useState(item?.importance ?? 1.0);
  const [importanceFloor, setImportanceFloor] = useState(
    item?.importance_floor ?? "",
  );
  const [pinned, setPinned] = useState(!!item?.pinned);
  const [evergreen, setEvergreen] = useState(item?.evergreen ?? true);
  const [archived, setArchived] = useState(!!item?.archived);
  const [sensitivity, setSensitivity] = useState(item?.sensitivity || "mundane");
  const [hint, setHint] = useState(item?.hint || "");
  const [endTime, setEndTime] = useState(
    item?.end_time ? new Date(item.end_time * 1000).toISOString().slice(0, 16) : "",
  );
  const [saving, setSaving] = useState(false);
  const canEditValue =
    !(item?.sensitivity === "secret" && (item?.encrypted || item?.decrypt_error));

  const handleSave = async () => {
    const nextKey = keyText.trim();
    if (!nextKey) {
      alert("Memory key is required");
      return;
    }
    let parsedValue = item?.value;
    if (canEditValue) {
      try {
        parsedValue = JSON.parse(valueText || "null");
      } catch {
        alert("Invalid JSON for value");
        return;
      }
    }
    const floorValue =
      importanceFloor === "" || importanceFloor === null
        ? undefined
        : Number.isNaN(parseFloat(importanceFloor))
        ? undefined
        : parseFloat(importanceFloor);
    const endSeconds = endTime ? Math.floor(new Date(endTime).getTime() / 1000) : null;
    setSaving(true);
    try {
      const ok = await onSave(
        {
          value: parsedValue,
          importance,
          importance_floor: floorValue,
          pinned,
          evergreen,
          archived,
          sensitivity,
          hint,
          end_time: endSeconds,
        },
        nextKey,
      );
      if (ok !== false) onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="memory-editor-overlay"
      role="presentation"
      onClick={() => {
        if (!saving) onClose();
      }}
    >
      <div
        className="memory-editor"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="memory-editor-header">
          <div>
            <p className="memory-editor-label">Memory key</p>
            <input
              type="text"
              className="memory-editor-key"
              value={keyText}
              onChange={(e) => setKeyText(e.target.value)}
              placeholder="memory key"
              disabled={saving}
            />
            <p className="memory-editor-meta">
              Created {toLocal(item?.created_at)} | Updated {toLocal(item?.updated_at)}{" "}
              | Last accessed {toLocal(item?.last_accessed)}
            </p>
          </div>
          <button
            type="button"
            className="memory-editor-close"
            aria-label="Close memory editor"
            onClick={onClose}
            disabled={saving}
          >
            &times;
          </button>
        </header>

        {item?.decrypt_error && (
          <div className="memory-editor-alert" role="alert">
            Unable to decrypt this value; editing the content is disabled.
          </div>
        )}

        <div className="memory-editor-grid">
          <label className="memory-field">
            <span>Importance</span>
            <input
              type="number"
              step={0.1}
              min={0}
              max={999}
              value={importance}
              onChange={(e) => setImportance(parseFloat(e.target.value || "0"))}
            />
          </label>
          <label className="memory-field">
            <span>Floor</span>
            <input
              type="number"
              step={0.1}
              min={0}
              value={importanceFloor}
              onChange={(e) => setImportanceFloor(e.target.value)}
              placeholder="importance floor"
            />
          </label>
          <label className="memory-field">
            <span>Sensitivity</span>
            <select
              value={sensitivity}
              onChange={(e) => setSensitivity(e.target.value)}
            >
              {SENSITIVITY_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="memory-field">
            <span>Hint</span>
            <input
              type="text"
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder="short context hint"
            />
          </label>
          <label className="memory-field">
            <span>End time</span>
            <input
              type="datetime-local"
              value={endTime}
              onChange={(e) => setEndTime(e.target.value)}
            />
          </label>
          <div className="memory-toggles">
            <label>
              <input
                type="checkbox"
                checked={pinned}
                onChange={(e) => setPinned(!!e.target.checked)}
              />
              pinned
            </label>
            <label>
              <input
                type="checkbox"
                checked={evergreen}
                onChange={(e) => setEvergreen(!!e.target.checked)}
              />
              evergreen
            </label>
            <label>
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => setArchived(!!e.target.checked)}
              />
              archived
            </label>
          </div>
        </div>

        <label className="memory-field memory-value-field">
          <span>Value</span>
          <textarea
            rows={10}
            value={valueText}
            onChange={(e) => setValueText(e.target.value)}
            disabled={!canEditValue}
            placeholder={canEditValue ? "JSON value" : "Value is redacted (secret)"}
          />
        </label>

        <div className="memory-editor-actions">
          <button type="button" className="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
};

const MemoryTab = ({ focusKey = null }) => {
  const [items, setItems] = useState([]);
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState(null); // { key, value, importance, pinned, importance_floor }
  const [selectedKey, setSelectedKey] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [ragBusy, setRagBusy] = useState(false);
  const [ragStatus, setRagStatus] = useState(null);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("{}");
  const [newImportance, setNewImportance] = useState(1.0);
  const [newEvergreen, setNewEvergreen] = useState(true);
  const [newEnd, setNewEnd] = useState(""); // datetime-local string
  const [newSensitivity, setNewSensitivity] = useState("mundane");
  const [newHint, setNewHint] = useState("");
  const [newPinned, setNewPinned] = useState(false);
  const [newImportanceFloor, setNewImportanceFloor] = useState("");
  const [sensFilter, setSensFilter] = useState(""); // empty = all
  const [memorizedFilter, setMemorizedFilter] = useState("");
  const [dateField, setDateField] = useState("updated_at");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sortBy, setSortBy] = useState("updated_at");
  const [sortDir, setSortDir] = useState("desc");
  const [rate, setRate] = useState(0.95);
  const [showArchived, setShowArchived] = useState(false);
  const [graphContexts, setGraphContexts] = useState({});
  const [graphExpandedKey, setGraphExpandedKey] = useState("");
  const [graphBusy, setGraphBusy] = useState(false);
  const [graphError, setGraphError] = useState("");
  const filterBarRef = useRef(null);
  const [filterBarHeight, setFilterBarHeight] = useState(0);

  const load = async (includeArchived = showArchived) => {
    try {
      const res = await axios.get("/api/memory", {
        params: {
          detailed: true,
          include_archived: !!includeArchived,
        },
      });
      const rows = (res.data?.items || []).map((it) => ({
        key: it.key,
        value: it.value,
        importance: it.importance ?? 1.0,
        created_at: it.created_at,
        updated_at: it.updated_at,
        last_accessed: it.last_accessed_at ?? it.last_accessed,
        evergreen: it.evergreen ?? true,
        end_time: it.end_time ?? null,
        archived: Boolean(it.archived ?? it.pruned_at),
        sensitivity: (it.sensitivity || "mundane").toLowerCase(),
        hint: it.hint || "",
        pinned: !!it.pinned,
        importance_floor: typeof it.importance_floor === 'number' ? it.importance_floor : null,
        vectorize: !!it.vectorize,
        vectorized_at: it.vectorized_at ?? null,
        rag_excluded: !!it.rag_excluded,
        rag_doc_id: it.rag_doc_id ?? null,
        encrypted: !!it.encrypted,
        decrypt_error: !!it.decrypt_error,
      }));
      setItems(rows);
      setGraphContexts({});
      setGraphExpandedKey("");
      setGraphError("");
    } catch {
      alert("Failed to load memory");
    }
  };

  useEffect(() => {
    load(showArchived);
  }, [showArchived]);

  useEffect(() => {
    if (!focusKey) return;
    const keyStr = String(focusKey);
    setFilter(keyStr);
    setSelectedKey(keyStr);
    setShowArchived(true);
  }, [focusKey]);

  useEffect(() => {
    const measure = () => {
      const height = filterBarRef.current?.offsetHeight || 0;
      setFilterBarHeight(height);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  useEffect(() => {
    if (!selectedKey) return undefined;
    const onKeyDown = (e) => {
      if (e.key === "Escape") setSelectedKey(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedKey]);

  useEffect(() => {
    setGraphExpandedKey("");
    setGraphError("");
  }, [selectedKey]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let rows = showArchived ? items : items.filter((r) => !r.archived);
    const fromTs = normalizeDateBoundary(dateFrom, "start");
    const toTs = normalizeDateBoundary(dateTo, "end");
    if (q) rows = rows.filter((r) => buildMemorySearchText(r).includes(q));
    if (sensFilter) rows = rows.filter((r) => r.sensitivity === sensFilter);
    if (memorizedFilter === "memorized") {
      rows = rows.filter((r) => isMemorized(r));
    } else if (memorizedFilter === "unmemorized") {
      rows = rows.filter((r) => !isMemorized(r));
    }
    if (fromTs != null || toTs != null) {
      rows = rows.filter((r) => {
        const ts = getMemoryFilterTimestamp(r, dateField);
        if (ts == null) return false;
        if (fromTs != null && ts < fromTs) return false;
        if (toTs != null && ts > toTs) return false;
        return true;
      });
    }
    // sort
    const dir = sortDir === "asc" ? 1 : -1;
    const keyMap = {
      key: (r) => r.key,
      importance: (r) => Number(r.importance || 0),
      importance_floor: (r) => (r.importance_floor ?? -Infinity),
      pinned: (r) => (r.pinned ? 1 : 0),
      created_at: (r) => Number(r.created_at || 0),
      updated_at: (r) => Number(r.updated_at || 0),
      end_time: (r) => Number(r.end_time || 0),
      sensitivity: (r) => r.sensitivity || "mundane",
    };
    const getter = keyMap[sortBy] || keyMap.updated_at;
    rows = [...rows].sort((a, b) => {
      const av = getter(a);
      const bv = getter(b);
      if (av === bv) return 0;
      return av > bv ? dir : -dir;
    });
    return rows;
  }, [
    dateField,
    dateFrom,
    dateTo,
    filter,
    memorizedFilter,
    sensFilter,
    sortBy,
    sortDir,
    items,
    showArchived,
  ]);

  const selected = useMemo(() => {
    if (!selectedKey) return null;
    return items.find((row) => row.key === selectedKey) || null;
  }, [items, selectedKey]);

  const selectedGraphContext = useMemo(() => {
    if (!selected) return null;
    return graphContexts[selected.key] || null;
  }, [graphContexts, selected]);

  useEffect(() => {
    if (!selectedKey) return undefined;
    const timer = window.setTimeout(() => {
      const rawKey = String(selectedKey);
      const escapedKey =
        typeof CSS !== "undefined" && typeof CSS.escape === "function"
          ? CSS.escape(rawKey)
          : rawKey.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      const row = document.querySelector(`[data-memory-key="${escapedKey}"]`);
      if (row && typeof row.scrollIntoView === "function") {
        row.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }, 30);
    return () => window.clearTimeout(timer);
  }, [selectedKey, filtered.length]);

  const isMemorized = (row) => Boolean(row?.vectorize || row?.vectorized_at);

  const excludeAllCount = useMemo(() => {
    return filtered.filter((row) => isMemorized(row) && !row.rag_excluded).length;
  }, [filtered]);

  const decayRate = useMemo(() => {
    const parsed = parseFloat(String(rate));
    return Number.isFinite(parsed) ? parsed : 0.95;
  }, [rate]);

  const valuePreview = (row) => {
    if (!row) return "";
    if (row.sensitivity === "secret" && (row.encrypted || row.decrypt_error)) {
      return "(secret value hidden)";
    }
    try {
      const raw = serializeMemoryValue(row);
      const compact = String(raw || "").replace(/\s+/g, " ").trim();
      if (compact.length <= 220) return compact;
      return `${compact.slice(0, 217)}...`;
    } catch {
      return String(row.value ?? "");
    }
  };

  const upsert = async (key, value, importance, evergreen, end_time, archived, sensitivity, hint, pinned, importanceFloor) => {
    try {
      const payload = {
        value,
        importance,
        evergreen,
        end_time,
        archived,
        sensitivity,
        hint,
      };
      if (typeof pinned === 'boolean') payload.pinned = pinned;
      if (importanceFloor !== undefined) payload.importance_floor = importanceFloor;
      await axios.post(`/api/memory/${encodeURIComponent(key)}`, payload);
      await load();
      return true;
    } catch {
      alert("Save failed");
      return false;
    }
  };

  const renameKey = async (currentKey, nextKey) => {
    const trimmed = String(nextKey || "").trim();
    if (!trimmed) {
      alert("Memory key is required");
      return false;
    }
    if (trimmed === currentKey) return true;
    try {
      await axios.post(`/api/memory/${encodeURIComponent(currentKey)}/rename`, {
        new_key: trimmed,
      });
      setSelectedKey((prev) => (prev === currentKey ? trimmed : prev));
      return true;
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) {
        alert("That memory key already exists.");
      } else if (status === 404) {
        alert("Memory not found.");
      } else {
        alert("Rename failed");
      }
      return false;
    }
  };

  const remove = async (key) => {
    const ok = window.confirm(
      `Delete "${key}"?\n\nThis removes it from the memory store and deletes any stored vectors for it.`,
    );
    if (!ok) return;
    try {
      await axios.delete(`/api/memory/${encodeURIComponent(key)}`);
      await load();
      setSelectedKey((prev) => (prev === key ? null : prev));
    } catch {
      alert("Delete failed");
    }
  };

  const setArchivedForKey = async (key, archived) => {
    try {
      await axios.post(`/api/memory/${encodeURIComponent(key)}/archive`, {
        archived: !!archived,
      });
      await load();
      if (archived && !showArchived) {
        setSelectedKey((prev) => (prev === key ? null : prev));
      }
    } catch (err) {
      console.error("archive failed", err);
      alert("Archive failed");
    }
  };

  const runDecay = async () => {
    try {
      await axios.post("/api/memory/decay", { rate: decayRate });
      await load();
    } catch {
      alert("Decay failed");
    }
  };

  const setMemorizedForKey = async (key, nextValue) => {
    if (!key) return;
    setRagBusy(true);
    setRagStatus(null);
    try {
      const res = await axios.post(
        `/api/memory/${encodeURIComponent(key)}/memorize`,
        { value: !!nextValue },
      );
      setRagStatus(res.data || null);
      await load();
    } catch (err) {
      console.error("memorize failed", err);
      alert("Memorize failed");
    } finally {
      setRagBusy(false);
    }
  };

  const setExcludedForKey = async (key, nextValue) => {
    if (!key) return;
    setRagBusy(true);
    setRagStatus(null);
    try {
      const res = await axios.post(
        `/api/memory/${encodeURIComponent(key)}/exclude`,
        { value: !!nextValue },
      );
      setRagStatus(res.data || null);
      await load();
    } catch (err) {
      console.error("exclude failed", err);
      alert("Exclude failed");
    } finally {
      setRagBusy(false);
    }
  };

  const loadGraphContext = async (key) => {
    if (!key) return;
    setGraphExpandedKey(key);
    if (graphContexts[key]) {
      setGraphError("");
      return;
    }
    setGraphBusy(true);
    setGraphError("");
    try {
      const res = await axios.get("/api/memory/graph", {
        params: {
          limit: Math.min(Math.max(items.length || 0, 72), 240),
          include_archived: true,
          focus_key: key,
        },
      });
      const context = buildGraphContext(res?.data?.graph || null, key);
      setGraphContexts((current) => ({
        ...current,
        [key]: context,
      }));
    } catch (err) {
      console.error("graph context load failed", err);
      setGraphError("Unable to load graph context.");
    } finally {
      setGraphBusy(false);
    }
  };

  const rehydrateRag = async () => {
    setRagBusy(true);
    setRagStatus(null);
    try {
      const res = await axios.post("/api/memory/rag/rehydrate", {
        allow_protected: false,
        allow_secret: false,
        include_archived: false,
        dry_run: false,
      });
      setRagStatus(res.data || null);
      await load();
    } catch (err) {
      console.error("rehydrate failed", err);
      alert("RAG rehydrate failed");
    } finally {
      setRagBusy(false);
    }
  };

  const excludeAllFiltered = async () => {
    const targets = filtered.filter((row) => isMemorized(row) && !row.rag_excluded);
    if (!targets.length) return;
    const ok = window.confirm(
      `Exclude ${targets.length} memories from default retrieval?\n\nThey stay stored + memorized, but will no longer appear in automatic RAG.`,
    );
    if (!ok) return;

    // NOTE: This loops per-key because the backend has no bulk endpoint yet.
    setRagBusy(true);
    setRagStatus(null);
    try {
      let updated = 0;
      let failed = 0;
      for (const row of targets) {
        try {
          await axios.post(`/api/memory/${encodeURIComponent(row.key)}/exclude`, {
            value: true,
          });
          updated += 1;
        } catch (err) {
          failed += 1;
          console.error("exclude failed", err);
        }
      }
      setRagStatus({
        status: failed ? `excluded ${updated} (failed ${failed})` : `excluded ${updated}`,
      });
      await load();
    } catch (err) {
      console.error("exclude all failed", err);
      alert("Exclude all failed");
    } finally {
      setRagBusy(false);
    }
  };

  return (
    <div
      className="memory-panel"
      style={{
        "--knowledge-filter-height": filterBarHeight ? `${filterBarHeight}px` : undefined,
      }}
    >
      <div ref={filterBarRef}>
        <FilterBar
          searchPlaceholder="Filter by key or value"
          searchValue={filter}
          onSearch={setFilter}
          right={
            <div className="memory-controls">
              <button
                type="button"
                className="chip memory-chip"
                onClick={rehydrateRag}
                disabled={ragBusy}
                title="Memorize all eligible memories so they can be found via semantic search"
              >
                {ragBusy ? "working..." : "memorize all"}
              </button>
              <button
                type="button"
                className="chip memory-chip"
                onClick={excludeAllFiltered}
                disabled={ragBusy || excludeAllCount === 0}
                title="Exclude all memorized memories in the current filter from default retrieval (keeps them stored + memorized)"
              >
                exclude all
              </button>
              <label className="memory-toggle">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(e) => setShowArchived(!!e.target.checked)}
                />
                show archived
              </label>
              <button
                type="button"
                className="chip memory-chip"
                onClick={runDecay}
                title={`Decay active memories (rate ${decayRate.toFixed(2)})`}
              >
                decay
              </button>
              <button
                type="button"
                className="chip memory-chip"
                onClick={() => setShowAdd((v) => !v)}
                title={showAdd ? "Close Add / Update panel" : "Open Add / Update panel"}
              >
                {showAdd ? "close" : "add"}
              </button>
              {ragStatus && (
                <span className="status-note">
                  {typeof ragStatus.reindexed === "number"
                    ? `memorized ${ragStatus.reindexed}`
                    : ragStatus.status || "updated"}
                </span>
              )}
            </div>
          }
        >
          <div className="memory-filter-row">
            <label className="memory-filter-label">
              sensitivity
              <select value={sensFilter} onChange={(e) => setSensFilter(e.target.value)}>
                <option value="">all</option>
                {SENSITIVITY_OPTIONS.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            <label className="memory-filter-label">
              memorized
              <select
                value={memorizedFilter}
                onChange={(e) => setMemorizedFilter(e.target.value)}
              >
                <option value="">all</option>
                <option value="memorized">yes</option>
                <option value="unmemorized">no</option>
              </select>
            </label>
            <label className="memory-filter-label">
              date field
              <select value={dateField} onChange={(e) => setDateField(e.target.value)}>
                <option value="updated_at">updated</option>
                <option value="created_at">created</option>
                <option value="last_accessed">accessed</option>
                <option value="end_time">end</option>
              </select>
            </label>
            <label className="memory-filter-label memory-filter-date">
              from
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </label>
            <label className="memory-filter-label memory-filter-date">
              to
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </label>
          </div>
        </FilterBar>
      </div>

      {showAdd && (
        <div className="memory-add" style={{ marginBottom: 12 }}>
          <div className="memory-add-header">
            <h3>Add / Update</h3>
            <span className="memory-decay">
              decay rate
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={rate}
                onChange={(e) => setRate(e.target.value)}
              />
            </span>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            type="text"
            placeholder="key"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            style={{ minWidth: 160 }}
          />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            evergreen
            <input type="checkbox" checked={newEvergreen} onChange={(e) => setNewEvergreen(!!e.target.checked)} />
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            pinned
            <input type="checkbox" checked={newPinned} onChange={(e) => setNewPinned(!!e.target.checked)} />
          </label>
          <label>
            floor
            <input
              type="number"
              min={0}
              step={0.1}
              value={newImportanceFloor}
              onChange={(e) => setNewImportanceFloor(e.target.value)}
              placeholder="importance floor"
              style={{ width: 120 }}
            />
          </label>
          <label>
            end
            <input type="datetime-local" value={newEnd} onChange={(e) => setNewEnd(e.target.value)} />
          </label>
          <label>
            sensitivity
            <select value={newSensitivity} onChange={(e) => setNewSensitivity(e.target.value)}>
              {SENSITIVITY_OPTIONS.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <input
            type="text"
            placeholder="hint (optional)"
            value={newHint}
            onChange={(e) => setNewHint(e.target.value)}
            style={{ minWidth: 160 }}
          />
          <input
            type="number"
            step={0.1}
            min={0}
            max={999}
            placeholder="importance"
            value={newImportance}
            onChange={(e) => setNewImportance(parseFloat(e.target.value || "1.0"))}
            style={{ width: 120 }}
          />
          <textarea
            placeholder='value (JSON)'
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            rows={2}
            style={{ flex: 1 }}
          />
          <button
            onClick={() => {
              try {
                const parsed = JSON.parse(newValue || "null");
                const endSecs = newEnd ? Math.floor(new Date(newEnd).getTime() / 1000) : undefined;
                let floorValue;
                if (newImportanceFloor === "") {
                  floorValue = undefined;
                } else {
                  const parsedFloor = parseFloat(newImportanceFloor);
                  floorValue = Number.isNaN(parsedFloor) ? undefined : parsedFloor;
                }
                upsert(newKey, parsed, newImportance, newEvergreen, endSecs, false, newSensitivity, newHint, newPinned, floorValue);
                setNewKey("");
                setNewValue("{}");
                setNewEvergreen(true);
                setNewPinned(false);
                setNewImportanceFloor("");
                setNewEnd("");
                setNewSensitivity("mundane");
                setNewHint("");
              } catch {
                alert("Invalid JSON for value");
              }
            }}
          >
            Save
          </button>
          </div>
        </div>
      )}

      {/* additional controls could go here if needed */}

      <div className="memory-table-pane">
        <table className="memory-table">
        <thead>
          <tr>
            <th onClick={() => { setSortBy('key'); setSortDir(sortBy==='key' && sortDir==='asc' ? 'desc':'asc'); }} style={{cursor:'pointer'}}>key</th>
            <th onClick={() => { setSortBy('sensitivity'); setSortDir(sortBy==='sensitivity' && sortDir==='asc' ? 'desc':'asc'); }} style={{cursor:'pointer'}}>sensitivity</th>
            <th onClick={() => { setSortBy('created_at'); setSortDir(sortBy==='created_at' && sortDir==='asc' ? 'desc':'asc'); }} style={{cursor:'pointer'}}>created</th>
            <th>value</th>
            <th>actions</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((row) => (
            <tr
              key={row.key}
              data-memory-key={row.key}
              className={selectedKey === row.key ? "selected" : ""}
              onClick={() => setSelectedKey((prev) => (prev === row.key ? null : row.key))}
            >
              <td>{row.key}</td>
              <td title={
                row.sensitivity === 'secret' ? 'secret: never exported; encrypt recommended' :
                row.sensitivity === 'protected' ? 'protected: excluded from external APIs by default' : row.sensitivity
              }>
                <span className={`sens-badge sens-${row.sensitivity}`}>{row.sensitivity}</span>
                {row.hint && <span className="hint-badge" title={`hint: ${row.hint}`}>?</span>}
                {isMemorized(row) && (
                  <span className="memory-status-badge" title="Memorized (stored for retrieval)">
                    mem
                  </span>
                )}
                {row.rag_excluded && (
                  <span className="memory-status-badge" title="Excluded from default retrieval">
                    excl
                  </span>
                )}
              </td>
              <td title={row.created_at ? toLocal(row.created_at) : undefined}>{toLocalDate(row.created_at)}</td>
              <td className="memory-value-cell" title={valuePreview(row)}>
                {row.sensitivity === 'secret' && (row.encrypted || row.decrypt_error) ? (
                  <span title={row.hint ? `hint: ${row.hint}` : 'secret value redacted'}>••• (secret)</span>
                ) : (
                  <span>{valuePreview(row)}</span>
                )}
              </td>
              <td>
                <div className="memory-actions">
                  <button
                    type="button"
                    className="chip memory-chip"
                    title="Edit: update this memory"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditing({
                        ...row,
                        valueText: JSON.stringify(row.value, null, 2),
                      });
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="chip memory-chip"
                    title={
                      isMemorized(row)
                        ? "Forget: delete vectors (keep memory stored)"
                        : "Memorize: add vectors for retrieval"
                    }
                    disabled={ragBusy}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMemorizedForKey(row.key, !isMemorized(row));
                    }}
                  >
                    {isMemorized(row) ? "Forget" : "Memorize"}
                  </button>
                  <button
                    type="button"
                    className="chip memory-chip"
                    title={
                      row.rag_excluded
                        ? "Include: allow retrieval again"
                        : "Exclude: keep memorized but omit from default retrieval"
                    }
                    disabled={!isMemorized(row) || ragBusy}
                    onClick={(e) => {
                      e.stopPropagation();
                      setExcludedForKey(row.key, !row.rag_excluded);
                    }}
                  >
                    {row.rag_excluded ? "Include" : "Exclude"}
                  </button>
                  <button
                    type="button"
                    className="chip memory-chip"
                    title="Delete: remove from memory store and delete vectors"
                    onClick={(e) => {
                      e.stopPropagation();
                      remove(row.key);
                    }}
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
        </table>
      </div>

      {selected ? (
        <div
          className="memory-details-overlay"
          role="presentation"
          onClick={() => setSelectedKey(null)}
        >
          <aside
            className="memory-details-pane floating"
            role="dialog"
            aria-label="Selected memory details"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="memory-details-header">
              <div>
                <p className="memory-editor-label">Selected memory</p>
                <h3 className="memory-editor-title">{selected.key}</h3>
              </div>
              <button
                type="button"
                className="memory-details-close"
                aria-label="Close memory details"
                onClick={() => setSelectedKey(null)}
              >
                &times;
              </button>
            </header>

            <div className="memory-details-actions">
              <button
                type="button"
                className="chip memory-chip"
                title="Edit: update value + metadata"
                onClick={() =>
                  setEditing({
                    ...selected,
                    valueText: JSON.stringify(selected.value, null, 2),
                  })
                }
              >
                Edit
              </button>
              <button
                type="button"
                className="chip memory-chip"
                disabled={ragBusy}
                onClick={() => setMemorizedForKey(selected.key, !isMemorized(selected))}
                title={
                  isMemorized(selected)
                    ? "Forget: delete vectors (keep memory stored)"
                    : "Memorize: add vectors for retrieval"
                }
              >
                {isMemorized(selected) ? "Forget" : "Memorize"}
              </button>
              <button
                type="button"
                className="chip memory-chip"
                disabled={!isMemorized(selected) || ragBusy}
                onClick={() => setExcludedForKey(selected.key, !selected.rag_excluded)}
                title={
                  selected.rag_excluded
                    ? "Include: allow retrieval again"
                    : "Exclude: keep memorized but omit from default retrieval"
                }
              >
                {selected.rag_excluded ? "Include" : "Exclude"}
              </button>
              <button
                type="button"
                className="chip memory-chip"
                title={
                  selected.archived
                    ? "Unarchive: restore to default view + retrieval"
                    : "Archive: hide from default view + retrieval"
                }
                onClick={() => setArchivedForKey(selected.key, !selected.archived)}
              >
                {selected.archived ? "Unarchive" : "Archive"}
              </button>
              <button
                type="button"
                className="chip memory-chip"
                onClick={() => {
                  if (graphExpandedKey === selected.key) {
                    setGraphExpandedKey("");
                    setGraphError("");
                    return;
                  }
                  loadGraphContext(selected.key);
                }}
                disabled={graphBusy && graphExpandedKey === selected.key}
                title="Append inferred graph context for this memory only when needed"
              >
                {graphBusy && graphExpandedKey === selected.key
                  ? "Loading..."
                  : graphExpandedKey === selected.key
                    ? "Hide graph"
                    : "Graph info"}
              </button>
              <button
                type="button"
                className="chip memory-chip"
                title="Delete: remove from memory store and delete vectors"
                onClick={() => remove(selected.key)}
              >
                Delete
              </button>
            </div>

            <dl className="memory-detail-grid">
              <div>
                <dt title="Whether this item is stored in the retrieval index">memorized</dt>
                <dd>{isMemorized(selected) ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt title="Exclude from default retrieval (keeps it stored)">excluded</dt>
                <dd>{selected.rag_excluded ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt title="Archived memories are hidden from default retrieval">archived</dt>
                <dd>{selected.archived ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt title="Controls what can be sent to external APIs">sensitivity</dt>
                <dd>{selected.sensitivity}</dd>
              </div>
              <div>
                <dt title="Higher importance increases retrieval priority">importance</dt>
                <dd>{Number(selected.importance || 0).toFixed(2)}</dd>
              </div>
              <div>
                <dt title="Minimum importance after decay">floor</dt>
                <dd>
                  {selected.importance_floor != null
                    ? Number(selected.importance_floor).toFixed(2)
                    : "-"}
                </dd>
              </div>
              <div>
                <dt title="Pinned memories do not decay">pinned</dt>
                <dd>{selected.pinned ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt title="Evergreen memories are retained longer">evergreen</dt>
                <dd>{selected.evergreen ? "yes" : "no"}</dd>
              </div>
              <div>
                <dt title="Optional expiry timestamp (local)">end</dt>
                <dd>{selected.end_time ? toLocal(selected.end_time) : "-"}</dd>
              </div>
              <div>
                <dt title="Created timestamp (local)">created</dt>
                <dd>{selected.created_at ? toLocal(selected.created_at) : "-"}</dd>
              </div>
              <div>
                <dt title="Last update timestamp (local)">updated</dt>
                <dd>{selected.updated_at ? toLocal(selected.updated_at) : "-"}</dd>
              </div>
              <div>
                <dt title="Last accessed timestamp (local)">accessed</dt>
                <dd>{selected.last_accessed ? toLocal(selected.last_accessed) : "-"}</dd>
              </div>
              <div>
                <dt title="Last time this was memorized into RAG">memorized at</dt>
                <dd>{selected.vectorized_at ? toLocal(selected.vectorized_at) : "-"}</dd>
              </div>
            </dl>

            <div className="memory-details-value">
              <p className="memory-editor-label">value</p>
              {selected.sensitivity === "secret" && (selected.encrypted || selected.decrypt_error) ? (
                <p className="status-note">
                  Secret value hidden{selected.hint ? ` (hint: ${selected.hint})` : ""}.
                </p>
              ) : (
                <pre>{JSON.stringify(selected.value, null, 2)}</pre>
              )}
            </div>

            <div className="memory-details-section">
              <div className="memory-section-header">
                <p className="memory-editor-label">graph context</p>
                <span className="status-note">
                  {graphExpandedKey === selected.key
                    ? "inferred from current memory graph"
                    : "append only when needed"}
                </span>
              </div>
              {graphExpandedKey !== selected.key ? (
                <p className="status-note">
                  Load graph info to inspect explicit anchors and nearby semantic links
                  for this memory without opening the visualization tab.
                </p>
              ) : graphBusy ? (
                <p className="status-note">Loading graph context...</p>
              ) : graphError ? (
                <p className="status-note warn">{graphError}</p>
              ) : !selectedGraphContext?.selectedNode ? (
                <p className="status-note">
                  No graph node was built for this memory in the current projection.
                </p>
              ) : (
                <>
                  <div className="memory-graph-summary">
                    <span className="memory-status-badge">
                      anchors {selectedGraphContext.anchors.length}
                    </span>
                    <span className="memory-status-badge">
                      threads {selectedGraphContext.threads.length}
                    </span>
                    <span className="memory-status-badge">
                      neighbors {selectedGraphContext.neighbors.length}
                    </span>
                    <span className="memory-status-badge">
                      signal {selectedGraphContext.metadata.signal_mode || "hybrid"}
                    </span>
                  </div>

                  <div className="memory-graph-block">
                    <p className="memory-editor-label">thread context</p>
                    {selectedGraphContext.threads.length ? (
                      <ul className="memory-graph-list">
                        {selectedGraphContext.threads.map((thread) => (
                          <li key={thread.id}>
                            <strong>{thread.label}</strong>
                            <span>
                              items {thread.itemCount || 0}
                              {thread.conversationCount
                                ? ` | conversations ${thread.conversationCount}`
                                : ""}
                            </span>
                            {thread.viaConversations?.length ? (
                              <small>
                                via {thread.viaConversations.join(", ")}
                                {thread.latestDate ? ` | latest ${thread.latestDate}` : ""}
                              </small>
                            ) : thread.latestDate ? (
                              <small>latest {thread.latestDate}</small>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="status-note">
                        No current thread summary rows reference this memory&apos;s
                        conversation anchors.
                      </p>
                    )}
                  </div>

                  <div className="memory-graph-block">
                    <p className="memory-editor-label">explicit anchors</p>
                    {selectedGraphContext.anchors.length ? (
                      <ul className="memory-graph-list">
                        {selectedGraphContext.anchors.map((anchor) => (
                          <li key={anchor.id}>
                            <strong>{anchor.label}</strong>
                            <span>{anchor.category}</span>
                            {anchor.refValue && anchor.refValue !== anchor.label ? (
                              <small>{anchor.refValue}</small>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="status-note">
                        No explicit anchors were inferred from this memory’s current
                        fields.
                      </p>
                    )}
                  </div>

                  <div className="memory-graph-block">
                    <p className="memory-editor-label">semantic neighbors</p>
                    {selectedGraphContext.neighbors.length ? (
                      <ul className="memory-graph-list">
                        {selectedGraphContext.neighbors.map((neighbor) => (
                          <li key={neighbor.id}>
                            <strong>{neighbor.label}</strong>
                            <span>
                              weight {neighbor.weight.toFixed(2)} | {neighbor.sensitivity}
                              {neighbor.memorized ? " | memorized" : ""}
                            </span>
                            {neighbor.sharedExplicitCount > 0 ? (
                              <small>
                                shared anchors {neighbor.sharedExplicitCount} | token overlap{" "}
                                {neighbor.tokenOverlap.toFixed(2)}
                              </small>
                            ) : (
                              <small>token overlap {neighbor.tokenOverlap.toFixed(2)}</small>
                            )}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="status-note">
                        No semantic neighbors crossed the current graph threshold.
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>
          </aside>
        </div>
      ) : (
        <p className="status-note">Select a memory row to see details.</p>
      )}

      {editing && (
        <MemoryEditor
          item={editing}
          onClose={() => setEditing(null)}
          onSave={async (payload, nextKey) => {
            let targetKey = editing.key;
            if (nextKey && nextKey !== editing.key) {
              const renamed = await renameKey(editing.key, nextKey);
              if (!renamed) return false;
              targetKey = nextKey;
            }
            return upsert(
              targetKey,
              payload.value,
              payload.importance,
              payload.evergreen,
              payload.end_time,
              payload.archived,
              payload.sensitivity,
              payload.hint,
              payload.pinned,
              payload.importance_floor,
            );
          }}
        />
      )}
    </div>
  );
};

export default MemoryTab;
