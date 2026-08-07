import React from "react";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import axios from "axios";

import KnowledgeVisualizationsTab from "../KnowledgeVisualizationsTab";

const d3Spies = vi.hoisted(() => ({
  zoomScaleBy: vi.fn(),
  zoomTransform: vi.fn(),
}));

vi.mock("d3", () => {
  const createSelection = () => {
    let boundData = [];
    const selection = {
      selectAll: () => createSelection(),
      remove: () => selection,
      attr: () => selection,
      append: () => createSelection(),
      text: () => selection,
      data(value) {
        if (arguments.length === 0) return boundData;
        boundData = Array.isArray(value) ? value : [];
        return selection;
      },
      join: () => selection,
      on: () => selection,
      call(handler, ...args) {
        if (typeof handler === "function") handler(selection, ...args);
        return selection;
      },
    };
    return selection;
  };
  const force = {
    id: () => force,
    distance: () => force,
    strength: () => force,
    radius: () => force,
  };
  const simulation = {
    alphaDecay: () => simulation,
    alphaMin: () => simulation,
    velocityDecay: () => simulation,
    force: () => simulation,
    on: () => simulation,
    alphaTarget: () => simulation,
    restart: () => simulation,
    stop: () => undefined,
  };
  const drag = () => {
    const handler = () => undefined;
    handler.on = () => handler;
    return handler;
  };
  const zoom = () => {
    const handler = () => undefined;
    handler.scaleExtent = () => handler;
    handler.filter = () => handler;
    handler.on = () => handler;
    handler.scaleBy = d3Spies.zoomScaleBy;
    handler.transform = d3Spies.zoomTransform;
    return handler;
  };
  const createTransform = (scale = 1) => ({
    k: scale,
    translate() {
      return this;
    },
    scale(nextScale) {
      this.k *= nextScale;
      return this;
    },
  });
  return {
    select: () => createSelection(),
    forceSimulation: () => simulation,
    forceLink: () => force,
    forceManyBody: () => force,
    forceX: () => force,
    forceY: () => force,
    forceCollide: () => force,
    drag,
    zoom,
    zoomIdentity: createTransform(),
  };
});

describe("KnowledgeVisualizationsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    d3Spies.zoomScaleBy.mockClear();
    d3Spies.zoomTransform.mockClear();
    localStorage.clear();
    vi.spyOn(axios, "post").mockResolvedValue({
      data: {
        status: "ok",
        graph_update: { node_count: 7, claim_count: 7 },
        revision: { action_id: "action-12345678" },
        graph: {
          nodes: [
            {
              id: "knowledge:person:self",
              node_id: "person:self",
              label: "Self",
              type: "person",
              node_kind: "entity",
              node_type: "person",
              level: 0,
              weight: 4,
              attributes: { relation_to_self: "self" },
            },
            {
              id: "knowledge:person:friend-maya",
              node_id: "person:friend-maya",
              label: "Maya Stone",
              type: "person",
              node_kind: "entity",
              node_type: "person",
              level: 0,
              weight: 1,
              attributes: { relation_to_self: "friend" },
            },
            {
              id: "knowledge:person:friend-jules",
              node_id: "person:friend-jules",
              label: "Jules Park",
              type: "person",
              node_kind: "entity",
              node_type: "person",
              level: 0,
              weight: 1,
              attributes: { relation_to_self: "friend" },
            },
            {
              id: "knowledge:person:coworker-ren",
              node_id: "person:coworker-ren",
              label: "Ren Ortiz",
              type: "person",
              node_kind: "entity",
              node_type: "person",
              level: 0,
              weight: 1,
              attributes: { relation_to_self: "co-worker" },
            },
            {
              id: "knowledge:person:family-lena",
              node_id: "person:family-lena",
              label: "Lena Rivera",
              type: "person",
              node_kind: "entity",
              node_type: "person",
              level: 0,
              weight: 1,
              attributes: { relation_to_self: "family" },
            },
            {
              id: "knowledge:org:computer-club",
              node_id: "org:computer-club",
              label: "Vancouver Computer Club",
              type: "organization",
              node_kind: "entity",
              node_type: "organization",
              level: 0,
              weight: 1,
              attributes: { organization_type: "club" },
            },
            {
              id: "knowledge:org:job",
              node_id: "org:job",
              label: "Float Systems Lab",
              type: "organization",
              node_kind: "entity",
              node_type: "organization",
              level: 0,
              weight: 2,
              attributes: { organization_type: "job" },
            },
          ],
          links: [
            {
              source: "knowledge:person:self",
              target: "knowledge:person:friend-maya",
              type: "claim",
              predicate: "friend_of",
              metadata: { relationship: "friend", relationship_strength: "close" },
              weight: 0.95,
            },
            {
              source: "knowledge:person:self",
              target: "knowledge:person:family-lena",
              type: "claim",
              predicate: "family_of",
              metadata: { relationship: "family" },
              weight: 0.96,
            },
            {
              source: "knowledge:person:friend-jules",
              target: "knowledge:org:computer-club",
              type: "claim",
              predicate: "member_of",
              metadata: { relationship: "club affiliation" },
              weight: 0.88,
            },
            {
              source: "knowledge:person:self",
              target: "knowledge:org:job",
              type: "claim",
              predicate: "works_at",
              metadata: { relationship: "job" },
              weight: 0.9,
            },
          ],
          metadata: {
            maxLevel: 0,
            node_count: 7,
            claim_count: 7,
            available: true,
          },
        },
      },
    });
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (String(url).includes("/api/threads/summary")) {
        return Promise.resolve({
          data: {
            summary: {
              thread_overview: {
                threads: [
                  {
                    label: "Design",
                    item_count: 3,
                    conversation_count: 1,
                    conversation_breakdown: [
                      {
                        conversation: "project/session-a.json",
                        item_count: 3,
                        latest_date: "2026-03-07",
                      },
                    ],
                  },
                ],
              },
            },
          },
        });
      }
      if (String(url).includes("/api/memory/graph")) {
        return Promise.resolve({
          data: {
            graph: {
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
                signal_mode: "hybrid",
                embeddings_source: "hash_fallback",
              },
            },
          },
        });
      }
      if (String(url).includes("/api/graph")) {
        return Promise.resolve({
          data: {
            graph: {
              nodes: [
                {
                  id: "knowledge:person:alice",
                  node_id: "person:alice",
                  label: "Alice Nguyen",
                  type: "person",
                  node_kind: "entity",
                  node_type: "person",
                  level: 0,
                  weight: 1,
                  attributes: {
                    city: "Vancouver",
                    role: "designer",
                    profile: {
                      interests: ["graphs", "design systems"],
                      contact: { website: "https://example.com/alice" },
                    },
                  },
                },
                {
                  id: "knowledge:person:bob",
                  node_id: "person:bob",
                  label: "Bob Patel",
                  type: "person",
                  node_kind: "entity",
                  node_type: "person",
                  level: 0,
                  weight: 1,
                  attributes: { city: "Seattle", role: "engineer" },
                },
              ],
              links: [
                {
                  source: "knowledge:person:alice",
                  target: "knowledge:person:bob",
                  type: "claim",
                  predicate: "friend_of",
                  weight: 0.95,
                  metadata: {
                    relationship: "friend",
                    evidence: { source: "social graph import", verified: true },
                  },
                },
              ],
              metadata: {
                maxLevel: 0,
                node_count: 2,
                claim_count: 1,
                available: true,
              },
            },
          },
        });
      }
      return Promise.reject(new Error(`unexpected url ${url}`));
    });
  });

  it("defaults to the stored knowledge graph and lazily loads other projections", async () => {
    render(<KnowledgeVisualizationsTab />);

    const knowledgeChoice = screen.getByRole("radio", { name: "Knowledge" });
    const threadsChoice = screen.getByRole("radio", { name: "Threads" });
    expect(knowledgeChoice).toHaveAttribute("aria-checked", "true");

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith(
        "/api/graph",
        expect.objectContaining({
          params: expect.objectContaining({ limit_nodes: 96, limit_claims: 192 }),
        }),
      );
    });
    expect(
      axios.get.mock.calls.some(([url]) => String(url).includes("/api/threads/summary")),
    ).toBe(false);
    expect(
      axios.get.mock.calls.some(([url]) => String(url).includes("/api/memory/graph")),
    ).toBe(false);

    fireEvent.click(threadsChoice);

    await waitFor(() => {
      expect(
        axios.get.mock.calls.some(([url]) => String(url).includes("/api/threads/summary")),
      ).toBe(true);
    });
    expect(JSON.parse(localStorage.getItem("float:knowledge-visualization:v1"))).toEqual(
      expect.objectContaining({ primaryGraphKey: "threads" }),
    );
  });

  it("finishes lazy graph loading when mounted under React Strict Mode", async () => {
    render(
      <React.StrictMode>
        <KnowledgeVisualizationsTab />
      </React.StrictMode>,
    );

    expect(await screen.findByText(/Stored graph: 2 nodes, 1 claims\./i)).toBeInTheDocument();
    expect(screen.queryByText(/Loading Knowledge/i)).not.toBeInTheDocument();
  });

  it("adds comparison layers lazily and exposes navigation controls", async () => {
    render(<KnowledgeVisualizationsTab />);

    await screen.findByText(/Stored graph: 2 nodes, 1 claims\./i);
    fireEvent.click(screen.getByText("Layers"));
    const memoryToggle = screen.getByRole("checkbox", { name: "Memory" });
    fireEvent.click(memoryToggle);

    await waitFor(() => {
      expect(memoryToggle).toBeChecked();
      expect(
        axios.get.mock.calls.some(([url]) => String(url).includes("/api/memory/graph")),
      ).toBe(true);
      expect(
        axios.get.mock.calls.find(([url]) => String(url).includes("/api/memory/graph"))?.[1],
      ).toEqual(
        expect.objectContaining({
          params: expect.objectContaining({ use_loaded_embeddings: false }),
        }),
      );
      expect(screen.getByLabelText(/layer spacing/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /increase memory level/i }));
    expect(screen.getByText("level 2/2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /zoom in/i }));
    expect(d3Spies.zoomScaleBy).toHaveBeenCalledWith(expect.anything(), 1.25);
    expect(screen.getByRole("button", { name: "Fit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset" })).toBeInTheDocument();
  });

  it("searches the current graph and opens an actionable node inspector", async () => {
    render(<KnowledgeVisualizationsTab />);

    await screen.findByText(/Stored graph: 2 nodes, 1 claims\./i);
    fireEvent.change(screen.getByRole("searchbox", { name: /search nodes/i }), {
      target: { value: "Alice" },
    });
    fireEvent.click(await screen.findByRole("option", { name: /Alice Nguyen/i }));

    expect(screen.getByRole("heading", { name: "Node" })).toBeInTheDocument();
    expect(screen.getByText("Alice Nguyen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Focus 1 hop/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bob Patel" })).toBeInTheDocument();
    expect(screen.getByText("graphs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://example.com/alice" })).toHaveAttribute(
      "href",
      "https://example.com/alice",
    );

    fireEvent.click(screen.getByRole("button", { name: /Inspect edge friend_of/i }));

    expect(screen.getByRole("heading", { name: "Relationship" })).toBeInTheDocument();
    expect(screen.getByText("social graph import")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /target Bob Patel/i }));
    expect(screen.getByText("Bob Patel")).toBeInTheDocument();
  });

  it("persists one central node per graph from the inspector", async () => {
    render(<KnowledgeVisualizationsTab />);

    await screen.findByText(/Stored graph: 2 nodes, 1 claims\./i);
    fireEvent.change(screen.getByRole("searchbox", { name: /search nodes/i }), {
      target: { value: "Alice" },
    });
    fireEvent.click(await screen.findByRole("option", { name: /Alice Nguyen/i }));
    const centralButton = screen.getByRole("button", { name: /Mark central/i });
    fireEvent.click(centralButton);

    expect(screen.getByRole("button", { name: "Central node" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem("float:knowledge-visualization:v1"))).toEqual(
        expect.objectContaining({
          centralNodeIds: expect.objectContaining({
            knowledge: "knowledge:person:alice",
          }),
        }),
      );
    });
  });

  it("applies a pasted manual graph update without exposing the dev sample", async () => {
    render(<KnowledgeVisualizationsTab />);

    await screen.findByText(/Stored graph: 2 nodes, 1 claims\./i);
    expect(
      screen.queryByRole("button", { name: /load 5-person sample/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Manage graph/i));
    fireEvent.click(screen.getByRole("button", { name: /paste json/i }));

    const editor = screen.getByLabelText(/graph update json/i);
    fireEvent.change(editor, {
      target: {
        value: JSON.stringify({
          source_kind: "manual",
          source_ref: "pasted_graph",
          nodes: [
            {
              ref: "self",
              node_id: "person:self",
              node_kind: "entity",
              node_type: "person",
              canonical_name: "Self",
            },
          ],
          claims: [
            {
              claim_id: "claim:self-note",
              claim_type: "profile",
              predicate: "has_note",
              roles: [{ role_name: "subject", node_ref: "self" }],
            },
          ],
        }),
      },
    });

    fireEvent.click(screen.getByRole("button", { name: /apply graph update/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/graph",
        expect.objectContaining({
          source_ref: "pasted_graph",
          nodes: expect.arrayContaining([
            expect.objectContaining({ node_id: "person:self" }),
          ]),
          claims: expect.arrayContaining([
            expect.objectContaining({ predicate: "has_note" }),
          ]),
        }),
      );
      expect(
        screen.getByText(/Applied 7 nodes and 7 claims; revision 12345678\./i),
      ).toBeInTheDocument();
    });

    expect(
      screen.getByText(/Stored graph: 7 nodes, 7 claims\./i),
    ).toBeInTheDocument();
  });
});
