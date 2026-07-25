import React from "react";
import { vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import axios from "axios";

import KnowledgeVisualizationsTab from "../KnowledgeVisualizationsTab";

vi.mock("d3", () => {
  const selection = {
    selectAll: () => selection,
    remove: () => selection,
    attr: () => selection,
    append: () => selection,
    text: () => selection,
    data: () => selection,
    join: () => selection,
    on: () => selection,
    call: () => selection,
  };
  const force = {
    id: () => force,
    distance: () => force,
    strength: () => force,
    radius: () => force,
  };
  const simulation = {
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
  return {
    select: () => selection,
    forceSimulation: () => simulation,
    forceLink: () => force,
    forceManyBody: () => force,
    forceX: () => force,
    forceY: () => force,
    forceCollide: () => force,
    drag,
  };
});

describe("KnowledgeVisualizationsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
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
                  attributes: { city: "Vancouver", role: "designer" },
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

  it("supports enabling multiple graph layers and level controls", async () => {
    render(<KnowledgeVisualizationsTab />);

    const memoryToggle = await screen.findByRole("checkbox", {
      name: /memory relation projection/i,
    });
    const threadToggle = screen.getByRole("checkbox", {
      name: /thread cluster projection/i,
    });
    const storedGraphToggle = screen.getByRole("checkbox", {
      name: /stored knowledge graph/i,
    });

    expect(threadToggle).toBeChecked();
    expect(memoryToggle).not.toBeChecked();
    expect(storedGraphToggle).not.toBeChecked();

    fireEvent.click(memoryToggle);
    fireEvent.click(storedGraphToggle);

    await waitFor(() => {
      expect(memoryToggle).toBeChecked();
      expect(threadToggle).toBeChecked();
      expect(storedGraphToggle).toBeChecked();
      expect(screen.getByLabelText(/plane offset/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /increase memory level/i }));

    expect(screen.getByText("level 2/2")).toBeInTheDocument();
    expect(
      screen.getByText(/thread and memory projections can be layered together/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Stored graph: 2 nodes, 1 claims\./i)).toBeInTheDocument();
  });

  it("applies a pasted manual graph update without exposing the dev sample", async () => {
    render(<KnowledgeVisualizationsTab />);

    await screen.findByRole("button", { name: /apply graph update/i });
    expect(
      screen.queryByRole("button", { name: /load 5-person sample/i }),
    ).not.toBeInTheDocument();
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
