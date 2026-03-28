import React from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import "@testing-library/jest-dom";
import axios from "axios";

vi.mock("../MemoryTab", () => ({
  default: () => <div>memory tab body</div>,
}));

vi.mock("../CalendarTab", () => ({
  default: () => <div>calendar tab body</div>,
}));

vi.mock("../ThreadsTab", () => ({
  default: () => <div>threads tab body</div>,
}));

vi.mock("../DocumentsTab", () => ({
  default: () => <div>documents tab body</div>,
}));

vi.mock("../KnowledgeVisualizationsTab", () => ({
  default: () => <div>visualizations tab body</div>,
}));

vi.mock("../KnowledgeSyncTab", () => ({
  default: () => <div>sync tab body</div>,
}));

vi.mock("../Skeleton", () => ({
  default: () => null,
  Line: () => null,
  Rect: () => null,
}));

let KnowledgeViewer;

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
};

beforeAll(async () => {
  KnowledgeViewer = (await import("../KnowledgeViewer")).default;
});

describe("KnowledgeViewer", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        ids: [],
        metadatas: [],
      },
    });
  });

  it("switches away from tab=threads without snapping back", async () => {
    render(
      <MemoryRouter initialEntries={["/knowledge?tab=threads"]}>
        <Routes>
          <Route
            path="/knowledge"
            element={
              <>
                <KnowledgeViewer />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    const threadsTabButton = await screen.findByRole("button", { name: /threads/i });
    expect(threadsTabButton).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("location-search")).toHaveTextContent("tab=threads");

    fireEvent.click(screen.getByRole("button", { name: /documents/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /documents/i })).toHaveAttribute(
        "aria-current",
        "page",
      );
      expect(screen.getByTestId("location-search")).toHaveTextContent("tab=documents");
    });
  });
});
