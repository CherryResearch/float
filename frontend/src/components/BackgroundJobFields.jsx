import React, { useEffect, useMemo, useState } from "react";
import {
  buildRecurrenceRule,
  defaultSeriesEndInput,
  normalizeBackgroundJobPolicy,
  parseRecurrenceRule,
  previewOccurrences,
  recurrenceSummary,
} from "../utils/backgroundJobPolicy";
import "../styles/BackgroundJobFields.css";

const BackgroundJobFields = ({
  rrule = "",
  onRruleChange,
  policy,
  onPolicyChange,
  startValue,
  timezone = "",
  compact = false,
  showExecutionPolicy = true,
}) => {
  const parsed = useMemo(
    () => parseRecurrenceRule(rrule, timezone),
    [rrule, timezone],
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const normalizedPolicy = useMemo(
    () => normalizeBackgroundJobPolicy(policy),
    [policy],
  );

  useEffect(() => {
    if (parsed.isCustom) setAdvancedOpen(true);
  }, [parsed.isCustom]);

  const updateRule = (updates) => {
    const next = buildRecurrenceRule({ ...parsed, ...updates, timeZone: timezone });
    onRruleChange?.(next);
  };
  const updatePolicy = (section, updates) => {
    onPolicyChange?.({
      ...normalizedPolicy,
      [section]: { ...normalizedPolicy[section], ...updates },
    });
  };
  const preview = previewOccurrences(startValue, rrule, 3, timezone);
  const stopCondition = normalizedPolicy.patience.stop_condition;

  return (
    <fieldset className={`background-job-fields${compact ? " is-compact" : ""}`}>
      <legend>{showExecutionPolicy ? "Background job" : "Schedule"}</legend>
      <p className="background-job-fields-copy">
        {showExecutionPolicy
          ? "Calendar controls when each occurrence starts. Additional patience and execution preferences are recorded for compatible agent runtimes."
          : "Calendar controls when this reminder occurs."}
      </p>

      <div className="background-job-fields-grid">
        <label>
          <span tabIndex={0} title="How often Calendar creates a runnable occurrence">
            Repeat
          </span>
          <select
            value={parsed.frequency}
            onChange={(event) => {
              const frequency = event.target.value;
              const shouldBoundDenseSeries =
                frequency === "minutes" && parsed.endMode === "never";
              updateRule({
                frequency,
                raw: frequency === "custom" ? parsed.raw : "",
                ...(shouldBoundDenseSeries
                  ? {
                      endMode: "until",
                      untilInput: defaultSeriesEndInput(
                        startValue,
                        frequency,
                        timezone,
                      ),
                    }
                  : {}),
              });
            }}
          >
            <option value="once">Once</option>
            <option value="minutes">Every N minutes</option>
            <option value="hours">Every N hours</option>
            <option value="days">Every N days</option>
            <option value="weeks">Every N weeks</option>
            <option value="custom">Custom RRULE</option>
          </select>
        </label>
        {!['once', 'custom'].includes(parsed.frequency) ? (
          <label>
            <span>Every</span>
            <input
              type="number"
              min="1"
              max="10080"
              value={parsed.interval}
              onChange={(event) => updateRule({ interval: event.target.value })}
            />
          </label>
        ) : null}
        {parsed.frequency !== "once" && parsed.frequency !== "custom" ? (
          <label>
            <span
              tabIndex={0}
              title="A run series can be open-ended, count-bounded, or date-bounded"
            >
              Series ends
            </span>
            <select
              value={parsed.endMode}
              onChange={(event) => {
                const endMode = event.target.value;
                updateRule({
                  endMode,
                  ...(endMode === "until" && !parsed.untilInput
                    ? {
                        untilInput: defaultSeriesEndInput(
                          startValue,
                          parsed.frequency,
                          timezone,
                        ),
                      }
                    : {}),
                });
              }}
            >
              <option value="never" disabled={parsed.frequency === "minutes"}>
                No end
              </option>
              <option value="count">After a run count</option>
              <option value="until">On a date</option>
            </select>
          </label>
        ) : null}
        {parsed.endMode === "count" && parsed.frequency !== "custom" ? (
          <label>
            <span>Run count</span>
            <input
              type="number"
              min="1"
              max="10000"
              value={parsed.count}
              onChange={(event) => updateRule({ count: event.target.value })}
            />
          </label>
        ) : null}
        {parsed.endMode === "until" && parsed.frequency !== "custom" ? (
          <label>
            <span>Last run by</span>
            <input
              type="datetime-local"
              value={parsed.untilInput}
              onChange={(event) => updateRule({ untilInput: event.target.value })}
            />
          </label>
        ) : null}
        {showExecutionPolicy ? (
          <label>
            <span
              tabIndex={0}
              title="Recorded per occurrence; multi-attempt agent runtimes apply it"
            >
              Patience
            </span>
            <select
              value={stopCondition}
              onChange={(event) => {
                const next = event.target.value;
                updatePolicy("patience", {
                  stop_condition: next,
                  max_attempts:
                    next === "one_pass"
                      ? 1
                      : Math.max(2, normalizedPolicy.patience.max_attempts || 3),
                });
              }}
            >
              <option value="one_pass">One pass</option>
              <option value="until_useful">Until useful</option>
              <option value="full_budget">Use full budget</option>
            </select>
          </label>
        ) : null}
        {showExecutionPolicy && stopCondition !== "one_pass" ? (
          <label>
            <span>Attempt limit</span>
            <input
              type="number"
              min="2"
              max="20"
              value={normalizedPolicy.patience.max_attempts}
              onChange={(event) =>
                updatePolicy("patience", {
                  max_attempts: Math.max(2, Number(event.target.value) || 2),
                })
              }
            />
          </label>
        ) : null}
        {showExecutionPolicy ? (
          <label>
            <span
              tabIndex={0}
              title="Retries only transient provider failures. They do not repeat a completed tool or count as workflow attempts."
            >
              Provider retries
            </span>
            <input
              type="number"
              min="0"
              max="10"
              value={normalizedPolicy.patience.max_provider_retries}
              onChange={(event) =>
                updatePolicy("patience", {
                  max_provider_retries: Math.max(
                    0,
                    Math.min(10, Number(event.target.value) || 0),
                  ),
                })
              }
            />
          </label>
        ) : null}
        {showExecutionPolicy ? (
          <label>
            <span
              tabIndex={0}
              title="Float stops waiting at this limit. Non-cooperative external tools may still finish outside the scheduler."
            >
              Wait cap (minutes)
            </span>
            <input
              type="number"
              min="1"
              max="1440"
              value={Math.max(
                1,
                Math.round(normalizedPolicy.patience.max_runtime_seconds / 60),
              )}
              onChange={(event) =>
                updatePolicy("patience", {
                  max_runtime_seconds:
                    Math.max(1, Number(event.target.value) || 1) * 60,
                })
              }
            />
          </label>
        ) : null}
      </div>

      <div className="background-job-summary" role="status">
        <strong>{recurrenceSummary(rrule, timezone)}</strong>
        {preview.length ? (
          <span>
            Next: {preview
              .map((date) =>
                date.toLocaleString([], timezone ? { timeZone: timezone } : {}),
              )
              .join(" · ")}
          </span>
        ) : null}
      </div>

      {showExecutionPolicy && stopCondition !== "one_pass" ? (
        <p className="background-job-fields-copy" role="note">
          Calendar prompt and tool actions currently run once per occurrence. Attempt and
          usefulness policy is retained for agent runtimes that support multi-attempt work.
        </p>
      ) : null}

      <button
        type="button"
        className="background-job-advanced-toggle"
        onClick={() => setAdvancedOpen((current) => !current)}
        aria-expanded={advancedOpen}
      >
        {advancedOpen
          ? showExecutionPolicy
            ? "Hide recorded policy"
            : "Hide advanced schedule"
          : showExecutionPolicy
            ? "Recorded execution policy and advanced schedule"
            : "Advanced schedule"}
      </button>
      {advancedOpen ? (
        <div className="background-job-advanced">
          {showExecutionPolicy ? (
            <>
          <label>
            <span tabIndex={0} title="Requested for compatible agent runtimes">
              Requested reasoning effort
            </span>
            <select
              value={normalizedPolicy.execution.reasoning_effort}
              onChange={(event) =>
                updatePolicy("execution", { reasoning_effort: event.target.value })
              }
            >
              <option value="inherit">Inherit default</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="xhigh">Extra high</option>
            </select>
          </label>
          <label>
            <span
              tabIndex={0}
              title="Use inherit or an exact model identifier; compatible agent runtimes apply it"
            >
              Requested model
            </span>
            <input
              type="text"
              value={normalizedPolicy.execution.model || "inherit"}
              placeholder="inherit or exact model id"
              onChange={(event) =>
                updatePolicy("execution", {
                  model: event.target.value.trimStart() || "inherit",
                })
              }
            />
          </label>
          <label>
            <span
              tabIndex={0}
              title="Optional internal workflow profile for a compatible agent runtime"
            >
              Requested workflow
            </span>
            <input
              type="text"
              value={normalizedPolicy.execution.workflow || "inherit"}
              placeholder="inherit, review, verify..."
              onChange={(event) =>
                updatePolicy("execution", {
                  workflow: event.target.value.trimStart() || "inherit",
                })
              }
            />
          </label>
          <label>
            <span
              tabIndex={0}
              title="Exact capabilities this scheduled job may use. Missing scopes pause the occurrence before anything runs."
            >
              Allowed permission scopes
            </span>
            <input
              type="text"
              value={normalizedPolicy.execution.permissions.join(", ")}
              placeholder="memory.write, files.read, web.read"
              onChange={(event) =>
                updatePolicy("execution", {
                  permissions: event.target.value
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
            />
            <small className="background-job-fields-hint">
              Allowed scopes apply to the whole scheduled job. Float pauses before a
              tool call when one of its required scopes is missing.
            </small>
          </label>
          <label className="background-job-checkbox">
            <input
              type="checkbox"
              checked={normalizedPolicy.execution.allow_subagents !== false}
              onChange={(event) =>
                updatePolicy("execution", { allow_subagents: event.target.checked })
              }
            />
            <span>Request bounded sub-agents</span>
          </label>
          <label className="background-job-checkbox">
            <input
              type="checkbox"
              checked={normalizedPolicy.execution.sandbox_processes !== false}
              onChange={(event) =>
                updatePolicy("execution", { sandbox_processes: event.target.checked })
              }
            />
            <span>Request sandboxed processes</span>
          </label>
          <div className="background-job-ownership-note" role="note">
            <strong>Ownership and lineage</strong>
            <span>
              {[
                normalizedPolicy.ownership.conversation_id
                  ? `chat ${normalizedPolicy.ownership.conversation_id}`
                  : "",
                normalizedPolicy.ownership.message_id
                  ? `message ${normalizedPolicy.ownership.message_id}`
                  : "",
                normalizedPolicy.ownership.parent_job_id
                  ? `parent job ${normalizedPolicy.ownership.parent_job_id}`
                  : "",
                normalizedPolicy.ownership.parent_agent_id
                  ? `parent agent ${normalizedPolicy.ownership.parent_agent_id}`
                  : "",
              ]
                .filter(Boolean)
                .join(" · ") || "No originating chat or parent job is recorded."}
            </span>
          </div>
          </>
          ) : null}
          <label className="background-job-raw-rule">
            <span>Advanced RRULE</span>
            <input
              type="text"
              value={rrule}
              onChange={(event) => onRruleChange?.(event.target.value)}
              placeholder="FREQ=DAILY;INTERVAL=1;COUNT=365"
            />
          </label>
        </div>
      ) : null}
    </fieldset>
  );
};

export default BackgroundJobFields;
