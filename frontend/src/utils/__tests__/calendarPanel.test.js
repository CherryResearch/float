import {
  buildMonthGridDates,
  formatStatusLabel,
  isClearedCalendarStatus,
} from "../calendarPanel";

describe("calendarPanel utils", () => {
  it("builds five- and six-week month grids without cropping", () => {
    expect(buildMonthGridDates(new Date("2025-04-01T12:00:00")).length).toBe(35);
    expect(buildMonthGridDates(new Date("2025-03-01T12:00:00")).length).toBe(42);
  });

  it("marks acknowledged and skipped events as cleared", () => {
    expect(isClearedCalendarStatus("acknowledged")).toBe(true);
    expect(isClearedCalendarStatus("skipped")).toBe(true);
    expect(isClearedCalendarStatus("pending")).toBe(false);
  });

  it("normalizes status labels for chips", () => {
    expect(formatStatusLabel("prompted")).toBe("prompted");
    expect(formatStatusLabel("follow_up_needed")).toBe("follow up needed");
  });
});
