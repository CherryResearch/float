import {
  buildCombinedGraphData,
  buildThreadGraph,
  getNodeFocus,
  hydrateMemoryGraph,
  normalizeConversationName,
} from "../knowledgeVisualization";

describe("knowledgeVisualization helpers", () => {
  it("normalizes conversation names consistently", () => {
    expect(normalizeConversationName("folder/demo.json#msg-2")).toBe("folder/demo");
    expect(normalizeConversationName("")).toBe("(unknown)");
  });

  it("builds a namespaced thread graph with conversation match keys", () => {
    const graph = buildThreadGraph({
      thread_overview: {
        threads: [
          {
            label: "Design",
            item_count: 4,
            conversation_count: 1,
            conversation_breakdown: [
              {
                conversation: "project/session-a.json",
                item_count: 4,
                latest_date: "2026-03-07",
              },
            ],
          },
        ],
      },
    });

    expect(graph.nodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: "threads:thread:Design",
          graphKey: "threads",
          level: 0,
        }),
        expect.objectContaining({
          id: "threads:conversation:project/session-a",
          matchKey: "conversation:project/session-a",
          level: 1,
        }),
      ]),
    );
    expect(graph.links).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "projection",
          source: "threads:thread:Design",
          target: "threads:conversation:project/session-a",
        }),
      ]),
    );
  });

  it("combines thread and memory graphs and emits cross-graph links for shared match keys", () => {
    const threadGraph = buildThreadGraph({
      thread_overview: {
        threads: [
          {
            label: "Design",
            item_count: 4,
            conversation_count: 1,
            conversation_breakdown: [
              {
                conversation: "project/session-a.json",
                item_count: 4,
              },
            ],
          },
        ],
      },
    });
    const memoryGraph = hydrateMemoryGraph({
      nodes: [
        {
          id: "memory:item:memory-one",
          label: "memory-one",
          type: "memory",
          level: 0,
          weight: 1,
        },
        {
          id: "memory:conversation:abc123",
          label: "project/session-a",
          type: "conversation_anchor",
          level: 1,
          weight: 2,
          match_key: "conversation:project/session-a",
        },
      ],
      links: [
        {
          source: "memory:item:memory-one",
          target: "memory:conversation:abc123",
          type: "explicit",
          category: "conversation",
          weight: 1,
        },
      ],
      metadata: {
        maxLevel: 1,
      },
    });

    expect(
      memoryGraph.nodes.find((node) => node.id === "memory:conversation:abc123")?.matchKey,
    ).toBe("conversation:project/session-a");

    const combined = buildCombinedGraphData({
      threadGraph,
      memoryGraph,
      includeThreadProjection: true,
      includeMemoryProjection: true,
      includeKnowledgeOverlay: false,
      levels: { threads: 0, memory: 1 },
      planeOffset: 0.35,
    });

    expect(combined.nodes).toHaveLength(4);
    expect(combined.crossLinks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "cross",
          matchKey: "conversation:project/session-a",
        }),
      ]),
    );
  });

  it("fades and shrinks nodes that are further from the focused level", () => {
    const activeNode = { id: "one", graphKey: "threads", level: 0 };
    const fadedNode = { id: "two", graphKey: "threads", level: 2 };

    expect(getNodeFocus(activeNode, { threads: 0 }, "")).toEqual({
      opacity: 1,
      scale: 1.08,
    });
    expect(getNodeFocus(fadedNode, { threads: 0 }, "")).toEqual({
      opacity: 0.3,
      scale: 0.76,
    });
  });
});
