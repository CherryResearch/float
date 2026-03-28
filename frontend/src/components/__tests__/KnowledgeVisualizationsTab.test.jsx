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

    expect(threadToggle).toBeChecked();
    expect(memoryToggle).not.toBeChecked();

    fireEvent.click(memoryToggle);

    await waitFor(() => {
      expect(memoryToggle).toBeChecked();
      expect(threadToggle).toBeChecked();
      expect(screen.getByLabelText(/plane offset/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /increase memory level/i }));

    expect(screen.getByText("level 2/2")).toBeInTheDocument();
    expect(
      screen.getByText(/thread and memory projections can be layered together/i),
    ).toBeInTheDocument();
  });
});
