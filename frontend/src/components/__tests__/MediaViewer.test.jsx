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

import MediaViewer, {
  buildMediaProvenanceRows,
  buildMediaStateInspectorRows,
} from "../MediaViewer";

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
    axiosMocks.get.mockResolvedValueOnce({
      data: {
        caption: "A small orange dog stands at the top of a wooden stair landing.",
        caption_status: "generated",
        caption_model: "local-captioner",
        caption_generated_at: "2026-04-23T17:20:00Z",
        index_status: "indexed",
        indexed_at: "2026-04-23T17:21:00Z",
        embedding_model: "clip:ViT-B-32",
        embedding_dim: 512,
      },
    });

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
              size: 228254,
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
    expect(screen.getByText(/223 KB/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /details/i }));
    expect(screen.getByText("Caption model")).toBeInTheDocument();
    expect(screen.getByText("local-captioner")).toBeInTheDocument();
    expect(screen.getByText("Index state")).toBeInTheDocument();
    expect(screen.getByText(/clip:ViT-B-32/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /explain media caption and index state/i }));
    expect(screen.getByRole("dialog", { name: /why this media state is shown/i })).toBeInTheDocument();
    expect(screen.getByText("Use details to inspect caption and retrieval metadata.")).toBeInTheDocument();
  });

  it("builds provenance rows from attachment metadata", () => {
    const rows = buildMediaProvenanceRows(
      {
        captionStatus: "manual",
        captionModel: "manual-caption",
        captionUpdatedAt: "2026-04-23T18:00:00Z",
        indexStatus: "indexed",
        embeddingModel: "clip:ViT-B-32",
        embeddingDim: 512,
      },
      {},
    );

    expect(rows.map((row) => row.label)).toEqual([
      "Caption status",
      "Caption model",
      "Caption edited",
      "Index state",
    ]);
    expect(rows.find((row) => row.label === "Index state")?.value).toBe(
      "indexed | model clip:ViT-B-32 | 512 dims",
    );
  });

  it("builds state-inspector rows from media provenance", () => {
    const rows = buildMediaStateInspectorRows(
      {
        contentHash: "abcdef1234567890",
        origin: "upload",
        captionStatus: "generated",
        captionModel: "captioner",
        indexStatus: "warning",
        indexWarning: "CLIP missing",
      },
      {},
    );

    expect(rows.find((row) => row.label === "Source")?.value).toBe("upload");
    expect(rows.find((row) => row.label === "Evidence")?.value).toBe("hash abcdef123456");
    expect(rows.find((row) => row.label === "Next")?.value).toMatch(/warnings/i);
  });
});
