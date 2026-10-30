import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import ToolPayloadView, { summarizeToolPayload } from "../ToolPayloadView";

afterEach(cleanup);

describe("ToolPayloadView recall results", () => {
  const recall = {
    status: "invoked",
    ok: true,
    message: null,
    data: {
      query: "user university degree program linguistics",
      mode: "hybrid",
      matches: [
        { id: "a", source: "memory:degree", snippet: "Studied linguistics", score: 0.3548 },
        { id: "b", source: "memory:program", snippet: "Degree program note", score: 0.3443 },
        { id: "c", source: "memory:university", snippet: "University note", score: 0.3438 },
      ],
      suggestions: ["degree"],
      suggestions_detail: [{ key: "degree", snippet: "Education note" }],
      recent_keys: ["degree"],
      image_matches: [],
      image_attachments: [],
    },
  };

  it.each([false, true])("shows wrapped recall matches (JSON string: %s)", (serialized) => {
    const value = serialized ? JSON.stringify(recall) : recall;
    render(<ToolPayloadView toolName="recall" value={value} />);
    expect(screen.getByText("3 matches")).toBeInTheDocument();
    expect(screen.getByText("Studied linguistics")).toBeInTheDocument();
    expect(screen.getByText("Degree program note")).toBeInTheDocument();
    expect(screen.getByText("University note")).toBeInTheDocument();
    expect(screen.getByText("Education note")).toBeInTheDocument();
    expect(screen.queryByText("No results.")).not.toBeInTheDocument();
    expect(summarizeToolPayload(value, "recall")).toBe("Recall: 3 matches");
  });

  it("shows a genuine empty recall and preserves error/suggestion details", () => {
    const { rerender } = render(
      <ToolPayloadView toolName="recall" value={{ query: "absent", matches: [] }} />,
    );
    expect(screen.getByText("No matching memories.")).toBeInTheDocument();
    rerender(<ToolPayloadView toolName="recall" value={{
      error: "not_found", requested_key: "absent", suggestions: ["available_note"],
    }} />);
    expect(screen.getByText("not_found")).toBeInTheDocument();
    expect(screen.getByText("available_note")).toBeInTheDocument();
    expect(screen.queryByText("No matching memories.")).not.toBeInTheDocument();
  });

  it("preserves image matches and attachment details", () => {
    render(<ToolPayloadView toolName="recall" value={{
      query: "cat", matches: [], image_matches: [{ snippet: "Cat on stairs" }],
      image_attachments: [{ name: "cat.png", url: "/api/attachments/cat/cat.png" }],
    }} />);
    expect(screen.getByText("Cat on stairs")).toBeInTheDocument();
    expect(screen.getByText(/cat\.png/)).toBeInTheDocument();
    expect(screen.queryByText("No matching memories.")).not.toBeInTheDocument();
  });

  it("keeps ordinary web search results and legitimate empty results", () => {
    const { rerender } = render(<ToolPayloadView toolName="web_search" value={{
      query: "Float", results: [{ title: "Float site", url: "https://example.com", snippet: "Overview" }],
    }} />);
    expect(screen.getByRole("link", { name: "Float site" })).toHaveAttribute("href", "https://example.com");
    expect(screen.getByText("Overview")).toBeInTheDocument();
    rerender(<ToolPayloadView toolName="web_search" value={{ query: "absent", results: [] }} />);
    expect(screen.getByText("No results.")).toBeInTheDocument();
  });

  it.each([
    ["web_search", { query: "Float", limit: 3, action: "search", scope: "workspace" }],
    ["recall", { key: "degree program", mode: "hybrid", top_k: 3, include_images: false }],
    ["web_search", { query: "Float", results: [], limit: 3, action: "search", scope: "workspace" }],
    ["recall", { query: "degree program", matches: [], mode: "hybrid", top_k: 3 }],
  ])("preserves every approval argument for %s", (toolName, args) => {
    const { container } = render(
      <ToolPayloadView toolName={toolName} kind="args" label="Arguments" value={args} />,
    );
    expect(screen.getByText("Arguments")).toBeInTheDocument();
    expect([...container.querySelectorAll("dt")].map((entry) => entry.textContent)).toEqual(Object.keys(args));
    expect([...container.querySelectorAll("dd")].map((entry) => entry.textContent)).toEqual(
      Object.values(args).map((value) => typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)),
    );
    expect(screen.queryByText("No results.")).not.toBeInTheDocument();
    expect(screen.queryByText("No matching memories.")).not.toBeInTheDocument();
    expect(container.querySelector(".tool-search-query")).not.toBeInTheDocument();
  });

  it("does not unwrap outcome-shaped approval arguments", () => {
    const args = { status: "invoked", ok: true, data: { scope: "workspace" } };
    const { container } = render(<ToolPayloadView toolName="recall" kind="args" value={args} />);
    expect([...container.querySelectorAll("dt")].map((entry) => entry.textContent)).toEqual(Object.keys(args));
    expect([...container.querySelectorAll("dd")][2].textContent).toBe(JSON.stringify(args.data, null, 2));
    expect(container.querySelector(".tool-payload-status")).not.toBeInTheDocument();
  });
});
