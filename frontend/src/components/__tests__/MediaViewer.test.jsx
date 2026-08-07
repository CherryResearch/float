import React, { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("axios", () => ({
  __esModule: true,
  default: axiosMocks,
  get: axiosMocks.get,
  post: axiosMocks.post,
  put: axiosMocks.put,
  delete: axiosMocks.delete,
}));

import MediaViewer, {
  buildMediaProvenanceRows,
  buildMediaStateInspectorRows,
} from "../MediaViewer";
import { captionGenerationErrorMessage } from "../../utils/mediaCaption";

describe("MediaViewer caption display", () => {
  beforeEach(() => {
    axiosMocks.get.mockReset();
    axiosMocks.post.mockReset();
    axiosMocks.put.mockReset();
    axiosMocks.delete.mockReset();
    axiosMocks.put.mockResolvedValue({ data: {} });
    axiosMocks.delete.mockResolvedValue({ data: {} });
    axiosMocks.get.mockResolvedValue({ data: {} });
    axiosMocks.post.mockResolvedValue({ data: {} });
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
    expect(screen.getByText("auto caption")).toBeInTheDocument();
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

  it("does not call a generic or unsaved URL a Float retrieval copy", () => {
    const rows = buildMediaProvenanceRows(
      {
        sourceUrl: "https://example.com/source-page",
        retrievalUrl: "blob:http://localhost/preview-id",
      },
      {},
    );

    expect(rows.find((row) => row.label === "Recorded source")).toBeTruthy();
    expect(rows.find((row) => row.label === "Float retrieval")).toBeUndefined();
  });

  it.each([
    ["Manual captions are protected; remove it explicitly first", /written manually/i],
    ["Caption generation is disabled", /captioning is off/i],
    ["The configured caption engine is not ready", /engine is not ready/i],
    ["Caption state conflict", /saved caption state changed/i],
  ])("maps caption conflict details to an actionable message: %s", (detail, expected) => {
    expect(
      captionGenerationErrorMessage({
        response: { status: 409, data: { detail } },
      }),
    ).toMatch(expected);
  });

  it("renders one caption-unavailable control while keeping CLIP state separate", async () => {
    axiosMocks.get.mockResolvedValueOnce({
      data: {
        caption: "Image attachment without generated caption.",
        caption_status: "placeholder",
        placeholder_caption: true,
        index_status: "indexed",
      },
    });

    render(
      <MemoryRouter>
        <MediaViewer
          src="/api/attachments/hash-2/photo.png"
          alt="photo.png"
          contextItems={[
            {
              src: "/api/attachments/hash-2/photo.png",
              alt: "photo.png",
              contentHash: "hash-2",
              caption: "Image attachment without generated caption.",
              captionStatus: "placeholder",
              placeholderCaption: true,
              indexStatus: "indexed",
              sourceUrl: "https://example.com/photo-page",
              sourceUrlRecordedAt: "2026-07-29T18:00:00Z",
              retrievalUrl: "/api/attachments/hash-2/photo.png",
            },
          ]}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open media viewer/i }));

    const unavailable = await screen.findByRole("button", {
      name: /caption unavailable.*clip image retrieval is indexed separately/i,
    });
    expect(screen.getAllByText("caption unavailable")).toHaveLength(1);
    expect(screen.queryByText(/^placeholder$/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "example.com" })).toHaveAttribute(
      "href",
      "https://example.com/photo-page",
    );
    expect(screen.getByRole("link", { name: /current api copy/i })).toHaveAttribute(
      "href",
      "/api/attachments/hash-2/photo.png",
    );

    fireEvent.mouseOver(unavailable);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /CLIP image retrieval is indexed separately/i,
    );
  });

  it("opens the manual editor from a gallery ref and retries one stored image", async () => {
    const viewerRef = createRef();
    const onAttachmentChange = vi.fn();
    axiosMocks.get.mockResolvedValueOnce({
      data: {
        caption: "Placeholder",
        caption_status: "placeholder",
        placeholder_caption: true,
        index_status: "indexed",
      },
    });
    axiosMocks.post.mockResolvedValueOnce({
      data: {
        status: "generated",
        attachment: {
          content_hash: "hash-3",
          filename: "owl.png",
          caption: "A barred owl sits beside a wooded ravine.",
          caption_status: "generated",
          placeholder_caption: false,
          index_status: "indexed",
        },
      },
    });

    render(
      <MemoryRouter>
        <MediaViewer
          ref={viewerRef}
          src="/api/attachments/hash-3/owl.png"
          alt="owl.png"
          contextItems={[
            {
              src: "/api/attachments/hash-3/owl.png",
              alt: "owl.png",
              contentHash: "hash-3",
              captionStatus: "placeholder",
              placeholderCaption: true,
              indexStatus: "indexed",
            },
          ]}
          onAttachmentChange={onAttachmentChange}
        />
      </MemoryRouter>,
    );

    act(() => viewerRef.current.editCaption());
    expect(await screen.findByRole("textbox", { name: /image caption/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry automatic caption/i }));

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith(
        "/api/attachments/caption/hash-3/generate",
        { replace_generated: false },
      );
    });
    expect(await screen.findByDisplayValue(/barred owl/i)).toBeInTheDocument();
    expect(onAttachmentChange).toHaveBeenCalledWith(
      expect.objectContaining({
        content_hash: "hash-3",
        caption_status: "generated",
      }),
    );
    expect(screen.getByText(/Removing a caption keeps the original file/i)).toBeInTheDocument();
  });

  it("clears the prior caption before loading another carousel item", async () => {
    axiosMocks.get
      .mockResolvedValueOnce({
        data: {
          caption: "First image caption.",
          caption_status: "manual",
          caption_model: "manual-caption",
        },
      })
      .mockImplementationOnce(() => new Promise(() => {}));

    render(
      <MemoryRouter>
        <MediaViewer
          src="/api/attachments/hash-a/first.png"
          alt="first.png"
          contextItems={[
            {
              src: "/api/attachments/hash-a/first.png",
              alt: "first.png",
              contentHash: "hash-a",
              caption: "First image caption.",
              captionStatus: "manual",
            },
            {
              src: "/api/attachments/hash-b/second.png",
              alt: "second.png",
              contentHash: "hash-b",
              caption: "",
              captionStatus: "missing",
            },
          ]}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open media viewer/i }));
    expect(await screen.findByText("First image caption.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /edit caption/i }));
    expect(screen.getByRole("textbox", { name: /image caption/i })).toHaveValue(
      "First image caption.",
    );

    fireEvent.click(screen.getByRole("button", { name: /next media/i }));

    await waitFor(() => {
      expect(screen.queryByText("First image caption.")).not.toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: /image caption/i })).toHaveValue("");
      expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
    });
  });

  it("explicitly replaces an existing generated caption when regenerated", async () => {
    axiosMocks.get.mockResolvedValueOnce({
      data: {
        caption: "An inaccurate generated caption.",
        caption_status: "generated",
        caption_model: "local-captioner",
      },
    });
    axiosMocks.post.mockResolvedValueOnce({
      data: {
        status: "generated",
        attachment: {
          content_hash: "hash-generated",
          caption: "A corrected generated caption.",
          caption_status: "generated",
        },
      },
    });

    render(
      <MemoryRouter>
        <MediaViewer
          src="/api/attachments/hash-generated/image.png"
          alt="image.png"
          contextItems={[
            {
              src: "/api/attachments/hash-generated/image.png",
              alt: "image.png",
              contentHash: "hash-generated",
              caption: "An inaccurate generated caption.",
              captionStatus: "generated",
            },
          ]}
        />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open media viewer/i }));
    fireEvent.click(await screen.findByRole("button", { name: /edit caption/i }));
    fireEvent.click(screen.getByRole("button", { name: /regenerate automatic caption/i }));

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith(
        "/api/attachments/caption/hash-generated/generate",
        { replace_generated: true },
      );
    });
    expect(await screen.findByDisplayValue("A corrected generated caption."))
      .toBeInTheDocument();
  });
});
