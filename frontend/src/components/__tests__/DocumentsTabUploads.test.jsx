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
