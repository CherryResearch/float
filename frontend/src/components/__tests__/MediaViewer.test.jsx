import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("axios", () => ({
  __esModule: true,
  default: axiosMocks,
  get: axiosMocks.get,
  put: axiosMocks.put,
  delete: axiosMocks.delete,
}));

import MediaViewer from "../MediaViewer";

describe("MediaViewer caption display", () => {
  beforeEach(() => {
    axiosMocks.get.mockReset();
    axiosMocks.put.mockReset();
    axiosMocks.delete.mockReset();
    axiosMocks.put.mockResolvedValue({ data: {} });
    axiosMocks.delete.mockResolvedValue({ data: {} });
    axiosMocks.get.mockResolvedValue({ data: {} });
  });

  it("shows the readable caption with compact status badges", async () => {
    render(
      <MemoryRouter>
        <MediaViewer
          src="/api/attachments/hash-1/bails.jpg"
          alt="bails.jpg"
          contextItems={[
            {
              src: "/api/attachments/hash-1/bails.jpg",
              alt: "bails.jpg",
              label: "bails.jpg",
              contentHash: "hash-1",
              caption: "A small orange dog stands at the top of a wooden stair landing.",
              captionStatus: "generated",
              indexStatus: "indexed",
            },
          ]}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open media viewer/i }));

    await waitFor(() =>
      expect(screen.getByText("A small orange dog stands at the top of a wooden stair landing.")).toBeInTheDocument(),
    );
    expect(screen.getByText("generated")).toBeInTheDocument();
    expect(screen.queryByText(/caption: generated/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open in viewer/i })).toBeInTheDocument();
  });
});
