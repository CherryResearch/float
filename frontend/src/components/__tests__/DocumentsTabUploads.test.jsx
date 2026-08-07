import {
  buildAttachmentViewerItems,
  describeAttachmentCard,
  resolveFocusedDoc,
} from "../DocumentsTab";
import { safeAttachmentSourceUrl } from "../../utils/attachmentProvenance";

describe("DocumentsTab upload helpers", () => {
  it("keeps caption text separate from status chips for gallery cards", () => {
    const attachment = {
      content_hash: "hash-1",
      filename: "bails.jpg",
      url: "/api/attachments/hash-1/bails.jpg",
      size: 2810184,
      uploaded_at: "2026-03-09T19:17:27Z",
      origin: "upload",
      relative_path: "uploads/hash-1/bails.jpg",
      caption: "A small orange dog stands at the top of a wooden stair landing.",
      caption_status: "generated",
      index_status: "indexed",
      placeholder_caption: false,
    };

    const viewerItems = buildAttachmentViewerItems([attachment]);
    const card = describeAttachmentCard(attachment, "unsorted");

    expect(viewerItems[0]).toMatchObject({
      caption: "A small orange dog stands at the top of a wooden stair landing.",
      captionStatus: "generated",
      indexStatus: "indexed",
      origin: "upload",
      relativePath: "uploads/hash-1/bails.jpg",
    });
    expect(card.captionText).toBe(
      "A small orange dog stands at the top of a wooden stair landing.",
    );
    expect(card.badges.map((badge) => badge.label)).toEqual([
      "unsorted",
      "upload",
      "indexed",
      "auto caption",
    ]);
    expect(card.captionText).not.toContain("generated");
    expect(card.secondaryMeta.join(" | ")).toContain("uploads/hash-1/bails.jpg");
  });

  it("passes caption and index provenance into viewer items", () => {
    const attachment = {
      content_hash: "hash-2",
      filename: "desk.png",
      url: "/api/attachments/hash-2/desk.png",
      caption: "A desk with a notebook.",
      caption_model: "local-captioner",
      caption_status: "generated",
      caption_generated_at: "2026-04-23T17:20:00Z",
      index_status: "indexed",
      indexed_at: "2026-04-23T17:21:00Z",
      embedding_model: "clip:ViT-B-32",
      embedding_dim: 512,
      display_name: "Desk notes",
      folder: "Reference",
      source_url: "https://example.com/desk",
      source_url_recorded_at: "2026-07-29T18:00:00Z",
    };

    expect(buildAttachmentViewerItems([attachment])[0]).toMatchObject({
      captionModel: "local-captioner",
      captionGeneratedAt: "2026-04-23T17:20:00Z",
      indexedAt: "2026-04-23T17:21:00Z",
      embeddingModel: "clip:ViT-B-32",
      embeddingDim: 512,
      displayName: "Desk notes",
      folder: "Reference",
      sourceUrl: "https://example.com/desk",
      retrievalUrl: "/api/attachments/hash-2/desk.png",
    });
  });

  it("collapses placeholder metadata into one readable caption badge", () => {
    const card = describeAttachmentCard(
      {
        filename: "owl.png",
        caption: "Image attachment without generated caption.",
        caption_status: "placeholder",
        placeholder_caption: true,
        index_status: "indexed",
      },
      "wildlife",
    );

    expect(card.captionText).toBe("");
    expect(card.badges.map((badge) => badge.label)).toEqual([
      "wildlife",
      "indexed",
      "caption unavailable",
    ]);
    expect(card.badges.find((badge) => badge.key === "placeholder")?.tooltip).toMatch(
      /CLIP image retrieval is indexed separately/i,
    );
  });

  it("accepts passive http provenance but rejects embedded credentials", () => {
    expect(safeAttachmentSourceUrl("https://example.com/image/1")).toBe(
      "https://example.com/image/1",
    );
    expect(safeAttachmentSourceUrl("https://user:secret@example.com/image/1")).toBe("");
    expect(safeAttachmentSourceUrl("https://example.com/image/1?access_token=secret")).toBe("");
    expect(safeAttachmentSourceUrl("https://example.com/image/1#access_token=secret")).toBe("");
    for (const credentialUrl of [
      "https://example.com/image/1?password=secret",
      "https://example.com/image/1?clientSecret=secret",
      "https://example.com/image/1?session_id=secret",
      "https://example.com/image/1?jwt-token=secret",
      "https://example.com/image/1?bearerToken=secret",
      "https://example.com/image/1?sv=2026-01-01&sig=secret",
      "https://example.com/image/1#route?client%255Fsecret=secret",
    ]) {
      expect(safeAttachmentSourceUrl(credentialUrl)).toBe("");
    }
    expect(safeAttachmentSourceUrl("https://example.com/image/1?width=800")).toBe(
      "https://example.com/image/1?width=800",
    );
    expect(
      safeAttachmentSourceUrl("https://example.com/image/1?tokenizer=clip#session_type=preview"),
    ).toBe("https://example.com/image/1?tokenizer=clip#session_type=preview");
    expect(safeAttachmentSourceUrl("javascript:alert(1)")).toBe("");
  });

  it("matches focused docs by absolute file path when opening from work history links", () => {
    const docs = [
      {
        id: "doc-1",
        meta: {
          title: "Notes",
          source: "workspace/notes.md",
          path: "workspace/notes.md",
        },
        baseName: "notes.md",
        folderPath: "workspace",
        isFilesystem: true,
      },
    ];

    expect(
      resolveFocusedDoc(docs, "D:/notebooks/float/data/files/workspace/notes.md"),
    ).toMatchObject({ id: "doc-1" });
  });
});
