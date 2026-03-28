import React, { useEffect, useMemo, useState } from "react";

const isPlainObject = (value) =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const coerceNumber = (value, { integer = false } = {}) => {
  if (value === "" || value === null || value === undefined) return undefined;
  const parsed = integer ? parseInt(value, 10) : parseFloat(value);
  return Number.isNaN(parsed) ? undefined : parsed;
};

const normalizeArrayInput = (raw) => {
  if (Array.isArray(raw)) return raw;
  if (!raw) return [];
  return String(raw)
    .split(/\r?\n|,/g)
    .map((item) => item.trim())
    .filter(Boolean);
};

const fieldOrder = (schema) => {
  const props = schema?.properties && typeof schema.properties === "object" ? schema.properties : {};
  const required = Array.isArray(schema?.required) ? schema.required : [];
  const keys = Object.keys(props);
  keys.sort((a, b) => {
    const aReq = required.includes(a) ? 0 : 1;
    const bReq = required.includes(b) ? 0 : 1;
    if (aReq !== bReq) return aReq - bReq;
    return a.localeCompare(b);
  });
  return keys;
};

const renderLabel = (key, propSchema, required) => {
  const title = propSchema?.title || key;
  return required ? `${title} *` : title;
};

const ToolArgsForm = ({ schema, ui = {}, value, onChange, disabled = false }) => {
  const args = isPlainObject(value) ? value : {};
  const required = useMemo(
    () => (Array.isArray(schema?.required) ? schema.required : []),
    [schema?.required],
  );
  const properties = useMemo(() => {
    const props = schema?.properties;
    return props && typeof props === "object" ? props : {};
  }, [schema?.properties]);

  const keys = useMemo(() => fieldOrder(schema), [schema]);
  const [jsonErrors, setJsonErrors] = useState({});
  const [showAdvanced, setShowAdvanced] = useState(false);

  const advancedKeys = useMemo(() => {
    if (!ui || typeof ui !== "object") return [];
    return keys.filter((key) => {
      const config = ui[key];
      if (!config || typeof config !== "object") return false;
      return Boolean(config.advanced || config.secret);
    });
  }, [keys, ui]);

  const hasAdvancedValues = useMemo(
    () =>
      advancedKeys.some((key) => {
        const current = args[key];
        if (current === null || current === undefined) return false;
        if (typeof current === "string" && !current.trim()) return false;
        return true;
      }),
    [advancedKeys, args],
  );

  useEffect(() => {
    if (hasAdvancedValues) setShowAdvanced(true);
  }, [hasAdvancedValues]);

  const updateField = (key, next) => {
    if (disabled) return;
    const merged = { ...args };
    if (next === undefined) {
      delete merged[key];
    } else {
      merged[key] = next;
    }
    onChange?.(merged);
  };

  if (!schema || schema.type !== "object") {
    return (
      <p className="tool-editor-hint" style={{ marginTop: 0 }}>
        No schema available for this tool.
      </p>
    );
  }

  return (
    <div className="tool-args-form" aria-label="Tool arguments form">
      {advancedKeys.length > 0 && (
        <div className="tool-args-advanced-row">
          <button
            type="button"
            className="tool-advanced-toggle"
            onClick={() => setShowAdvanced((prev) => !prev)}
            aria-expanded={showAdvanced}
          >
            {showAdvanced ? "Hide advanced" : `Advanced options (${advancedKeys.length})`}
          </button>
        </div>
      )}
      {keys.map((key) => {
        const propSchema = properties[key] || {};
        const uiConfig = ui && typeof ui === "object" ? ui[key] || {} : {};
        const isRequired = required.includes(key);
        const isAdvanced = Boolean(uiConfig?.advanced || uiConfig?.secret);
        const rawType = propSchema?.type;
        const type = Array.isArray(rawType) ? rawType[0] : rawType;
        const description = propSchema?.description;
        const hasEnum = Array.isArray(propSchema?.enum) && propSchema.enum.length > 0;
        const current = args[key];
        const hasValue =
          current !== null &&
          current !== undefined &&
          !(typeof current === "string" && !current.trim());

        if (isAdvanced && !showAdvanced && !isRequired && !hasValue) {
          return null;
        }

        const renderInput = () => {
          if (hasEnum) {
            const asString = current == null ? "" : String(current);
            return (
              <select
                value={asString}
                onChange={(event) => updateField(key, event.target.value || undefined)}
                disabled={disabled}
              >
                <option value="">Select...</option>
                {propSchema.enum.map((option) => (
                  <option key={String(option)} value={String(option)}>
                    {String(option)}
                  </option>
                ))}
              </select>
            );
          }

          if (type === "boolean") {
            return (
              <label className="tool-boolean-field">
                <input
                  type="checkbox"
                  checked={Boolean(current)}
                  onChange={(event) => updateField(key, event.target.checked)}
                  disabled={disabled}
                />
                <span>{renderLabel(key, propSchema, isRequired)}</span>
              </label>
            );
          }

          if (type === "integer" || type === "number") {
            const numberValue =
              typeof current === "number" && Number.isFinite(current) ? String(current) : "";
            return (
              <input
                type="number"
                value={numberValue}
                min={propSchema?.minimum}
                max={propSchema?.maximum}
                step={type === "integer" ? 1 : "any"}
                onChange={(event) =>
                  updateField(
                    key,
                    coerceNumber(event.target.value, { integer: type === "integer" }),
                  )
                }
                placeholder={propSchema?.default != null ? String(propSchema.default) : undefined}
                disabled={disabled}
              />
            );
          }

          if (type === "array" && propSchema?.items?.type === "string") {
            const normalized = normalizeArrayInput(current);
            return (
              <textarea
                rows={3}
                value={normalized.join("\n")}
                onChange={(event) => updateField(key, normalizeArrayInput(event.target.value))}
                placeholder="One per line (or comma-separated)"
                disabled={disabled}
              />
            );
          }

          if (type === "object" || type === "array") {
            const textValue =
              current == null
                ? ""
                : typeof current === "string"
                  ? current
                  : JSON.stringify(current, null, 2);
            const fieldError = jsonErrors[key];
            return (
              <>
                <textarea
                  rows={uiConfig.rows || 6}
                  value={textValue}
                  onChange={(event) => {
                    const text = event.target.value;
                    if (!text.trim()) {
                      setJsonErrors((prev) => ({ ...prev, [key]: "" }));
                      updateField(key, undefined);
                      return;
                    }
                    try {
                      const parsed = JSON.parse(text);
                      setJsonErrors((prev) => ({ ...prev, [key]: "" }));
                      updateField(key, parsed);
                    } catch (err) {
                      setJsonErrors((prev) => ({ ...prev, [key]: "Invalid JSON." }));
                    }
                  }}
                  spellCheck={false}
                  disabled={disabled}
                />
                {fieldError && <div className="tool-field-error">{fieldError}</div>}
              </>
            );
          }

          const textValue = current == null ? "" : String(current);
          const multiline = Boolean(uiConfig.multiline);
          const secret = Boolean(uiConfig.secret);
          if (multiline) {
            return (
              <textarea
                rows={uiConfig.rows || 4}
                value={textValue}
                onChange={(event) =>
                  updateField(key, event.target.value === "" ? undefined : event.target.value)
                }
                placeholder={propSchema?.default != null ? String(propSchema.default) : undefined}
                disabled={disabled}
              />
            );
          }
          return (
            <input
              type={secret ? "password" : "text"}
              value={textValue}
              onChange={(event) =>
                updateField(key, event.target.value === "" ? undefined : event.target.value)
              }
              placeholder={propSchema?.default != null ? String(propSchema.default) : undefined}
              disabled={disabled}
            />
          );
        };

        if (type === "boolean") {
          return (
            <div key={key} className="tool-field">
              {renderInput()}
              {description && <small className="tool-field-description">{description}</small>}
            </div>
          );
        }

        return (
          <div key={key} className="tool-field">
            <span>{renderLabel(key, propSchema, isRequired)}</span>
            {renderInput()}
            {description && <small className="tool-field-description">{description}</small>}
          </div>
        );
      })}
    </div>
  );
};

export default ToolArgsForm;
