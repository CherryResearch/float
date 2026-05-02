import {
  buildAttachmentViewerItems,
  describeAttachmentCard,
  resolveFocusedDoc,
} from "../DocumentsTab";

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
      "generated",
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
    };

    expect(buildAttachmentViewerItems([attachment])[0]).toMatchObject({
      captionModel: "local-captioner",
      captionGeneratedAt: "2026-04-23T17:20:00Z",
      indexedAt: "2026-04-23T17:21:00Z",
      embeddingModel: "clip:ViT-B-32",
      embeddingDim: 512,
    });
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
