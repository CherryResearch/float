import React from "react";
import { render, fireEvent, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { MemoryRouter } from "react-router-dom";

import RagContextPanel, { normalizeRagMatches } from "../RagContextPanel";

describe("normalizeRagMatches", () => {
  it("returns sanitized entries with sensible fallbacks", () => {
    const result = normalizeRagMatches([
      {
        id: "  doc-1  ",
        text: "  Alpha   beta ",
        source: " notes ",
        score: 0.9876,
      },
      {
        metadata: { source: "ref", id: "meta-1" },
      },
    ]);
    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({
      id: "doc-1",
      text: "Alpha beta",
      source: "notes",
      score: 0.9876,
    });
    expect(result[1]).toMatchObject({
      id: "meta-1",
      source: "ref",
    });
  });

  it("handles non-arrays gracefully", () => {
    expect(normalizeRagMatches(null)).toEqual([]);
    expect(normalizeRagMatches(undefined)).toEqual([]);
  });
});

describe("RagContextPanel", () => {
  const sampleMatches = [
    {
      id: "one",
      source: "notes",
      text: "Alpha entry",
      score: 0.52,
      metadata: { embedding_model: "local:all-MiniLM-L6-v2" },
    },
    { id: "two", source: "kb", text: "Bravo entry", score: 0.12 },
  ];

  it("renders matches and toggles visibility", () => {
    render(
      <MemoryRouter>
        <RagContextPanel matches={sampleMatches} defaultOpen />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: /Retrieved context/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("Alpha entry")).toBeInTheDocument();
    const score = screen.getByText(/sim 0.52/i);
    expect(score).toHaveAttribute(
      "title",
      expect.stringContaining("Embedding: local:all-MiniLM-L6-v2"),
    );
    const toggle = screen.getByRole("button", { name: /Retrieved context/i });
    fireEvent.click(toggle);
    expect(screen.queryByText("Alpha entry")).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText("Bravo entry")).toBeInTheDocument();
  });

  it("omits rendering when no matches are provided", () => {
    const { container } = render(
      <MemoryRouter>
        <RagContextPanel matches={[]} />
      </MemoryRouter>,
    );
    expect(container.firstChild).toBeNull();
  });
});
