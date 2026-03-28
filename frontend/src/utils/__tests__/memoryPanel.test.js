import {
  buildGraphContext,
  buildMemorySearchText,
  getMemoryFilterTimestamp,
  normalizeDateBoundary,
  serializeMemoryValue,
} from "../memoryPanel";

describe("memoryPanel utils", () => {
  it("includes key, hint, and value in the memory search text", () => {
    expect(
      buildMemorySearchText({
        key: "sibling_note",
        hint: "family",
        value: { name: "Alice" },
      }),
    ).toContain("alice");
  });

  it("uses the hint instead of secret ciphertext for hidden secret values", () => {
    expect(
      serializeMemoryValue({
        value: "should-not-leak",
        hint: "billing token",
        sensitivity: "secret",
        encrypted: true,
      }),
    ).toBe("billing token");
  });

  it("normalizes inclusive date boundaries and timestamps", () => {
    expect(normalizeDateBoundary("2024-04-01", "start")).toBeLessThan(
      normalizeDateBoundary("2024-04-01", "end"),
    );
    expect(
      getMemoryFilterTimestamp({ updated_at: 1713139200 }, "updated_at"),
    ).toBe(1713139200);
  });

  it("builds explicit anchors and semantic neighbors for a selected memory", () => {
    const context = buildGraphContext(
      {
        nodes: [
          { id: "memory:item:sibling_note", label: "sibling_note", type: "memory" },
          {
            id: "memory:namespace:1",
            label: "family/session-a",
            category: "conversation",
            ref_value: "family/session-a",
            type: "conversation_anchor",
          },
          {
            id: "memory:thread:1",
            label: "Family Planning",
            type: "thread",
            item_count: 2,
            conversation_count: 1,
            latest_date: "2026-03-21",
          },
          {
            id: "memory:item:favorite_color",
            label: "favorite_color",
            type: "memory",
            sensitivity: "mundane",
            memorized: false,
            importance: 1,
          },
        ],
        links: [
          {
            source: "memory:item:sibling_note",
            target: "memory:namespace:1",
            type: "explicit",
            weight: 1,
          },
          {
            source: "memory:namespace:1",
            target: "memory:thread:1",
            type: "projection",
            category: "thread",
            weight: 2,
          },
          {
            source: "memory:item:sibling_note",
            target: "memory:item:favorite_color",
            type: "semantic",
            weight: 0.61,
            token_overlap: 0.2,
            shared_explicit_count: 0,
          },
        ],
        metadata: {
          signal_mode: "hybrid",
        },
      },
      "sibling_note",
    );

    expect(context.anchors).toHaveLength(1);
    expect(context.anchors[0].label).toBe("family/session-a");
    expect(context.threads).toHaveLength(1);
    expect(context.threads[0].label).toBe("Family Planning");
    expect(context.neighbors).toHaveLength(1);
    expect(context.neighbors[0].label).toBe("favorite_color");
    expect(context.metadata.signal_mode).toBe("hybrid");
  });
});
