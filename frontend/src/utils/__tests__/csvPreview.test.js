import {
  detectDelimiter,
  formatDelimiterLabel,
  parseCsvPreview,
  shouldUseFirstRowAsHeader,
} from "../csvPreview";

describe("csvPreview helpers", () => {
  it("detects comma-delimited data with quoted cells", () => {
    const text = [
      'destination,notes,cost',
      '"Paris, France","Window seat requested",1200',
      '"Kyoto","Needs ""vegetarian"" plan",980',
    ].join("\n");

    expect(detectDelimiter(text)).toBe(",");

    const preview = parseCsvPreview(text, { maxRows: 10, maxColumns: 10 });

    expect(preview.rows).toEqual([
      ["destination", "notes", "cost"],
      ["Paris, France", "Window seat requested", "1200"],
      ["Kyoto", 'Needs "vegetarian" plan', "980"],
    ]);
    expect(preview.totalRows).toBe(3);
    expect(preview.totalColumns).toBe(3);
  });

  it("detects tab-delimited data and preview truncation", () => {
    const text = [
      "city\tcountry\tstatus",
      "Paris\tFrance\tbooked",
      "Kyoto\tJapan\tplanned",
    ].join("\n");

    const preview = parseCsvPreview(text, { maxRows: 2, maxColumns: 2 });

    expect(detectDelimiter(text)).toBe("\t");
    expect(preview.rows).toEqual([
      ["city", "country"],
      ["Paris", "France"],
    ]);
    expect(preview.truncatedRows).toBe(true);
    expect(preview.truncatedColumns).toBe(true);
    expect(formatDelimiterLabel(preview.delimiter)).toBe("tab");
  });

  it("prefers using the first row as a header when it looks column-like", () => {
    expect(
      shouldUseFirstRowAsHeader([
        ["destination", "travel date", "cost"],
        ["Paris", "2026-04-01", "1200"],
      ]),
    ).toBe(true);

    expect(
      shouldUseFirstRowAsHeader([
        ["1200", "42", "3.14"],
        ["980", "12", "8.20"],
      ]),
    ).toBe(false);
  });
});
