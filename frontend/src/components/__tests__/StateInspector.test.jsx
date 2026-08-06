import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it } from "vitest";

import StateInspector, {
  buildStateInspectorTitle,
  normalizeStateInspectorRows,
} from "../StateInspector";

describe("StateInspector", () => {
  it("normalizes rows and builds a compact title", () => {
    expect(
      normalizeStateInspectorRows([
        { label: "Source", value: "sync overview" },
        { label: "Empty", value: "" },
        null,
      ]),
    ).toEqual([{ label: "Source", value: "sync overview" }]);

    expect(
      buildStateInspectorTitle({
        title: "Why this state is shown",
        summary: "Local evidence",
        rows: [{ label: "Source", value: "sync overview" }],
      }),
    ).toBe("Why this state is shown | Local evidence | Source: sync overview");
  });

  it("opens a compact evidence panel", () => {
    render(
      <StateInspector
        title="Why this tool is here"
        summary="The assistant proposed this tool."
        rows={[
          { label: "Source", value: "api gpt-5.4" },
          { label: "Next", value: "Approve, edit, or deny this tool." },
        ]}
      />,
    );

    const button = screen.getByRole("button", { name: "Why this tool is here" });
    expect(button).toHaveAttribute("title", expect.stringContaining("Source: api gpt-5.4"));

    fireEvent.click(button);

    expect(screen.getByRole("dialog", { name: "Why this tool is here" })).toBeInTheDocument();
    expect(screen.getByText("The assistant proposed this tool.")).toBeInTheDocument();
    expect(screen.getByText("Approve, edit, or deny this tool.")).toBeInTheDocument();
  });

  it("portals a top-placed panel outside clipping containers", () => {
    const { container } = render(
      <div style={{ overflow: "hidden" }}>
        <StateInspector
          title="Message metadata"
          summary="Routing details"
          rows={[{ label: "Model", value: "gpt-5.6" }]}
          placement="top"
        />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Message metadata" }));

    const dialog = screen.getByRole("dialog", { name: "Message metadata" });
    expect(dialog).toHaveAttribute("data-placement", "top");
    expect(dialog.parentElement).toBe(document.body);
    expect(container).not.toContainElement(dialog);
  });
});
