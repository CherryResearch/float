import { describe, expect, it } from "vitest";
import {
  buildRecurrenceRule,
  normalizeBackgroundJobPolicy,
  parseRecurrenceRule,
  previewOccurrences,
  recurrenceSummary,
} from "../backgroundJobPolicy";
import { dateToZonedInput, defaultSeriesEndInput } from "../zonedDateTime";

describe("background job policy", () => {
  it("keeps schedule, patience, execution, and ownership independent", () => {
    const policy = normalizeBackgroundJobPolicy({
      patience: { stop_condition: "until_useful", max_attempts: 4 },
      execution: { reasoning_effort: "high", allow_subagents: false },
      ownership: { conversation_id: "chat-1" },
    });

    expect(policy.patience).toMatchObject({
      stop_condition: "until_useful",
      max_attempts: 4,
      max_provider_retries: 2,
      max_runtime_seconds: 900,
    });
    expect(policy.execution).toMatchObject({
      reasoning_effort: "high",
      model: "inherit",
      workflow: "inherit",
      allow_subagents: false,
      sandbox_processes: true,
    });
    expect(policy.ownership.conversation_id).toBe("chat-1");
  });

  it("builds and previews bounded two-minute recurrence", () => {
    const rule = buildRecurrenceRule({
      frequency: "minutes",
      interval: 2,
      endMode: "count",
      count: 30,
    });
    const parsed = parseRecurrenceRule(rule);
    const preview = previewOccurrences("2026-07-25T20:00:00", rule, 3);

    expect(rule).toBe("FREQ=MINUTELY;INTERVAL=2;COUNT=30");
    expect(parsed).toMatchObject({
      frequency: "minutes",
      interval: 2,
      endMode: "count",
      count: 30,
    });
    expect(preview).toHaveLength(3);
    expect(preview[1].getTime() - preview[0].getTime()).toBe(120_000);
    expect(recurrenceSummary(rule)).toBe("Every 2 minutes · 30 runs");
  });

  it("builds UNTIL from the selected event timezone", () => {
    const rule = buildRecurrenceRule({
      frequency: "days",
      interval: 1,
      endMode: "until",
      untilInput: "2026-07-25T20:00",
      timeZone: "America/Vancouver",
    });

    expect(rule).toBe("FREQ=DAILY;INTERVAL=1;UNTIL=20260726T030000Z");
    expect(parseRecurrenceRule(rule, "America/Vancouver").untilInput).toBe(
      "2026-07-25T20:00",
    );
  });

  it("keeps unsupported custom RRULE clauses raw", () => {
    const raw = "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=12";
    const parsed = parseRecurrenceRule(raw, "UTC");

    expect(parsed).toMatchObject({ frequency: "custom", isCustom: true, raw });
    expect(buildRecurrenceRule({ ...parsed, interval: 2, timeZone: "UTC" })).toBe(raw);
  });

  it("keeps daily previews and bounded defaults on the selected wall clock across DST", () => {
    const timeZone = "America/Vancouver";
    const rule = "FREQ=DAILY;INTERVAL=1;COUNT=3";
    const preview = previewOccurrences("2026-03-07T20:00:00", rule, 3, timeZone);

    expect(preview.map((item) => dateToZonedInput(item, timeZone))).toEqual([
      "2026-03-07T20:00",
      "2026-03-08T20:00",
      "2026-03-09T20:00",
    ]);
    expect(defaultSeriesEndInput("2026-03-07T20:00:00", "days", timeZone)).toBe(
      "2026-04-06T20:00",
    );
  });

  it("uses the next compatible instant for a DST gap without shifting later days", () => {
    const timeZone = "America/Vancouver";
    const preview = previewOccurrences(
      "2027-03-13T02:30:00",
      "FREQ=DAILY;INTERVAL=1;COUNT=3",
      3,
      timeZone,
    );

    expect(preview.map((item) => dateToZonedInput(item, timeZone))).toEqual([
      "2027-03-13T02:30",
      "2027-03-14T03:30",
      "2027-03-15T02:30",
    ]);
  });
});
