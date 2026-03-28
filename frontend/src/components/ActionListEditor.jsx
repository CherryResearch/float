import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";

import ToolArgsForm from "./ToolArgsForm";
import "../styles/ActionListEditor.css";

const normalizeActions = (value) => {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => item && typeof item === "object" && !Array.isArray(item));
};

const ensureActionId = (action) => {
  if (action.id || action.request_id) return action;
  return {
    ...action,
    id: `act-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  };
};

const safeString = (value) => {
  if (typeof value !== "string") return "";
  return value;
};

const validateArgsAgainstSchema = (schema, args) => {
  if (!schema || schema.type !== "object") return { ok: true };
  const props =
    schema.properties && typeof schema.properties === "object" ? schema.properties : {};
  const required = Array.isArray(schema.required) ? schema.required : [];
  const missing = required.filter((key) => {
    const value = args?.[key];
    if (value === null || value === undefined) return true;
    if (typeof value === "string" && !value.trim()) return true;
    return false;
  });
  if (missing.length) {
    return { ok: false, message: `Missing required argument(s): ${missing.join(", ")}` };
  }
  return { ok: true };
};

const isToolAction = (action) =>
  String(action?.kind || action?.type || "").toLowerCase() === "tool";

const isPromptAction = (action) =>
  String(action?.kind || action?.type || "").toLowerCase() === "prompt";

const normalizeConversationMode = (value, fallback = "new_chat") => {
  const raw = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, "_");
  if (
    ["inline", "current_chat", "current_thread", "same_chat", "same_thread"].includes(raw)
  ) {
    return "inline";
  }
  if (
    ["new", "new_chat", "new_thread", "separate_chat", "separate_thread"].includes(raw)
  ) {
    return "new_chat";
  }
  return fallback;
};

const normalizeActionConversation = (
  action,
  { defaultConversationMode = "new_chat", inlineConversation = null } = {},
) => {
  const normalized = { ...(action || {}) };
  const fallbackMode = inlineConversation ? defaultConversationMode : "new_chat";
  const conversationMode = normalizeConversationMode(
    normalized.conversation_mode || normalized.run_target || normalized.target,
    normalized.session_id ? "inline" : fallbackMode,
  );
  normalized.conversation_mode = conversationMode;
  if (conversationMode === "inline" && inlineConversation?.session_id) {
    if (!normalized.session_id) {
      normalized.session_id = inlineConversation.session_id;
    }
    if (!normalized.message_id && inlineConversation.message_id) {
      normalized.message_id = inlineConversation.message_id;
    }
    if (!normalized.chain_id && inlineConversation.chain_id) {
      normalized.chain_id = inlineConversation.chain_id;
    }
  } else if (conversationMode !== "inline") {
    delete normalized.session_id;
    delete normalized.message_id;
    delete normalized.chain_id;
  }
  return normalized;
};

const buildDefaultArgs = (schema) => {
  if (!schema || schema.type !== "object") return {};
  const props =
    schema.properties && typeof schema.properties === "object" ? schema.properties : {};
  const out = {};
  Object.entries(props).forEach(([key, propSchema]) => {
    if (!propSchema || typeof propSchema !== "object") return;
    if (Object.prototype.hasOwnProperty.call(propSchema, "default")) {
      out[key] = propSchema.default;
    }
  });
  return out;
};

const ActionListEditor = ({
  actions,
  onChange,
  onValidationChange,
  disabled = false,
  title = "Actions",
  addPlaceholder = "Type a tool name, or type a prompt…",
  defaultConversationMode = "new_chat",
  inlineConversation = null,
}) => {
  const [toolSpecs, setToolSpecs] = useState([]);
  const [loadingSpecs, setLoadingSpecs] = useState(false);
  const [specsError, setSpecsError] = useState("");
  const [draft, setDraft] = useState("");
  const [expanded, setExpanded] = useState({});

  const normalizedActions = useMemo(
    () =>
      normalizeActions(actions).map((action) =>
        normalizeActionConversation(action, {
          defaultConversationMode,
          inlineConversation,
        }),
      ),
    [actions, defaultConversationMode, inlineConversation],
  );

  useEffect(() => {
    let cancelled = false;
    const fetchSpecs = async () => {
      setLoadingSpecs(true);
      setSpecsError("");
      try {
        const res = await axios.get("/api/tools/specs");
        const tools = Array.isArray(res.data?.tools) ? res.data.tools : [];
        if (!cancelled) setToolSpecs(tools);
      } catch (err) {
        if (!cancelled) {
          const detail =
            err?.response?.data?.detail || err?.response?.data?.message || err?.message;
          setSpecsError(String(detail || "Unable to load tool schemas."));
        }
      } finally {
        if (!cancelled) setLoadingSpecs(false);
      }
    };
    fetchSpecs();
    return () => {
      cancelled = true;
    };
  }, []);

  const toolSpecByName = useMemo(() => {
    const map = new Map();
    toolSpecs.forEach((spec) => {
      if (!spec || typeof spec !== "object") return;
      if (typeof spec.name !== "string" || !spec.name.trim()) return;
      map.set(spec.name.trim(), spec);
    });
    return map;
  }, [toolSpecs]);

  const actionErrors = useMemo(() => {
    const errors = [];
    normalizedActions.forEach((action) => {
      if (!action) return;
      if (isToolAction(action)) {
        const name = safeString(action.name).trim();
        if (!name) {
          errors.push("Tool action is missing a tool name.");
          return;
        }
        const spec = toolSpecByName.get(name);
        const schema = spec?.parameters;
        const args = action.args && typeof action.args === "object" && !Array.isArray(action.args)
          ? action.args
          : {};
        const validation = validateArgsAgainstSchema(schema, args);
        if (!validation.ok) {
          errors.push(`${name}: ${validation.message}`);
        }
      } else if (isPromptAction(action)) {
        const prompt = safeString(action.prompt).trim();
        if (!prompt) errors.push("Prompt action is missing text.");
      } else {
        errors.push("Unknown action kind (expected tool or prompt).");
      }
    });
    return errors;
  }, [normalizedActions, toolSpecByName]);

  useEffect(() => {
    if (!onValidationChange) return;
    onValidationChange({
      ok: actionErrors.length === 0,
      errors: actionErrors,
    });
  }, [actionErrors, onValidationChange]);

  const updateActions = (next) => {
    const normalized = normalizeActions(next).map((action) =>
      ensureActionId(
        normalizeActionConversation(action, {
          defaultConversationMode,
          inlineConversation,
        }),
      ),
    );
    onChange?.(normalized);
  };

  const addAction = () => {
    const text = draft.trim();
    if (!text) return;
    const knownTool = toolSpecByName.has(text);
    const next = [...normalizedActions];
    if (knownTool) {
      const spec = toolSpecByName.get(text);
      next.push(
        ensureActionId({
          kind: "tool",
          name: text,
          args: buildDefaultArgs(spec?.parameters),
          status: "scheduled",
        }),
      );
    } else {
      next.push(
        ensureActionId({
          kind: "prompt",
          prompt: text,
          status: "scheduled",
        }),
      );
    }
    setDraft("");
    updateActions(next);
  };

  const moveAction = (idx, direction) => {
    const next = [...normalizedActions];
    const target = idx + direction;
    if (target < 0 || target >= next.length) return;
    const [removed] = next.splice(idx, 1);
    next.splice(target, 0, removed);
    updateActions(next);
  };

  const removeAction = (idx) => {
    const next = [...normalizedActions];
    next.splice(idx, 1);
    updateActions(next);
  };

  const toggleExpanded = (id) => {
    if (!id) return;
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const updateActionField = (idx, patch) => {
    const next = [...normalizedActions];
    next[idx] = ensureActionId({ ...next[idx], ...patch });
    updateActions(next);
  };

  return (
    <section className="actions-editor" aria-label={title}>
      <div className="actions-editor-header">
        <h4>{title}</h4>
        <div className="actions-editor-meta">
          {loadingSpecs ? "Loading tools…" : specsError ? specsError : null}
        </div>
      </div>

      <div className="actions-editor-add">
        <input
          type="text"
          value={draft}
          onChange={(evt) => setDraft(evt.target.value)}
          placeholder={addPlaceholder}
          list="actions-editor-tool-list"
          disabled={disabled}
          onKeyDown={(evt) => {
            if (evt.key === "Enter") {
              evt.preventDefault();
              addAction();
            }
          }}
        />
        <datalist id="actions-editor-tool-list">
          {Array.from(toolSpecByName.keys()).map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <button type="button" onClick={addAction} disabled={disabled || !draft.trim()}>
          Add
        </button>
      </div>

      {normalizedActions.length === 0 ? (
        <p className="actions-editor-empty">No actions yet.</p>
      ) : (
        <ul className="actions-editor-list">
          {normalizedActions.map((action, idx) => {
            const id = String(action.id || action.request_id || idx);
            const kind = String(action.kind || action.type || "").toLowerCase();
            const isExpanded = expanded[id] ?? idx === 0;
            const headerLabel = (() => {
              if (kind === "tool") {
                const name = safeString(action.name).trim() || "tool";
                return `tool: ${name}`;
              }
              if (kind === "prompt") {
                return "prompt";
              }
              return "action";
            })();

            return (
              <li key={id} className="actions-editor-item">
                <div className="actions-editor-item-header">
                  <button
                    type="button"
                    className="actions-editor-expand"
                    aria-expanded={isExpanded}
                    onClick={() => toggleExpanded(id)}
                    disabled={disabled}
                  >
                    {isExpanded ? "▾" : "▸"}
                  </button>
                  <div className="actions-editor-item-title">{headerLabel}</div>
                  <div className="actions-editor-item-controls">
                    <button
                      type="button"
                      onClick={() => moveAction(idx, -1)}
                      disabled={disabled || idx === 0}
                      title="Move up"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={() => moveAction(idx, 1)}
                      disabled={disabled || idx === normalizedActions.length - 1}
                      title="Move down"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      onClick={() => removeAction(idx)}
                      disabled={disabled}
                      title="Remove"
                    >
                      ✕
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="actions-editor-item-body">
                    {isToolAction(action) ? (
                      <>
                        <label className="actions-editor-field">
                          <span>Tool</span>
                          <input
                            type="text"
                            value={safeString(action.name)}
                            onChange={(evt) => updateActionField(idx, { name: evt.target.value })}
                            list="actions-editor-tool-list"
                            disabled={disabled}
                          />
                        </label>
                        <div className="actions-editor-field">
                          <span>Args</span>
                          <ToolArgsForm
                            schema={toolSpecByName.get(safeString(action.name).trim())?.parameters}
                            ui={toolSpecByName.get(safeString(action.name).trim())?.metadata?.ui}
                            value={
                              action.args && typeof action.args === "object" && !Array.isArray(action.args)
                                ? action.args
                                : {}
                            }
                            onChange={(nextArgs) => updateActionField(idx, { args: nextArgs })}
                          />
                        </div>
                        <label className="actions-editor-field">
                          <span>Follow-up prompt (optional)</span>
                          <textarea
                            rows={3}
                            value={safeString(action.prompt)}
                            onChange={(evt) => updateActionField(idx, { prompt: evt.target.value })}
                            placeholder="After the tool runs, send this prompt into chat…"
                            disabled={disabled}
                          />
                        </label>
                        <label className="actions-editor-field">
                          <span>Run response in</span>
                          <select
                            value={normalizeConversationMode(
                              action.conversation_mode,
                              defaultConversationMode,
                            )}
                            onChange={(evt) =>
                              updateActionField(idx, {
                                conversation_mode: normalizeConversationMode(
                                  evt.target.value,
                                  defaultConversationMode,
                                ),
                              })
                            }
                            disabled={disabled}
                          >
                            <option value="inline" disabled={!inlineConversation?.session_id}>
                              Current chat
                            </option>
                            <option value="new_chat">New chat</option>
                          </select>
                          <small className="actions-editor-hint">
                            Choose whether the follow-up response stays in this chat or runs in its own task chat.
                          </small>
                        </label>
                      </>
                    ) : (
                      <>
                        <label className="actions-editor-field">
                          <span>Prompt</span>
                          <textarea
                            rows={4}
                            value={safeString(action.prompt)}
                            onChange={(evt) => updateActionField(idx, { prompt: evt.target.value })}
                            placeholder="Ask Float to do something…"
                            disabled={disabled}
                          />
                        </label>
                        <label className="actions-editor-field">
                          <span>Run response in</span>
                          <select
                            value={normalizeConversationMode(
                              action.conversation_mode,
                              defaultConversationMode,
                            )}
                            onChange={(evt) =>
                              updateActionField(idx, {
                                conversation_mode: normalizeConversationMode(
                                  evt.target.value,
                                  defaultConversationMode,
                                ),
                              })
                            }
                            disabled={disabled}
                          >
                            <option value="inline" disabled={!inlineConversation?.session_id}>
                              Current chat
                            </option>
                            <option value="new_chat">New chat</option>
                          </select>
                          <small className="actions-editor-hint">
                            Choose whether the response stays in this chat or opens a separate task chat.
                          </small>
                        </label>
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

export default ActionListEditor;
