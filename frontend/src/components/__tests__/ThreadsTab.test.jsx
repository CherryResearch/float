import React from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";

vi.mock("../../main", async () => {
  const ReactModule = await import("react");
  return {
    GlobalContext: ReactModule.createContext({
      setState: vi.fn(),
    }),
  };
});

let ThreadsTab;

const summaryFixture = {
  tag_counts: { alpha: 2, beta: 1 },
  clusters: { "0": "planning" },
  conversations: {
    "sess-1": { nugget_count: 2, topics: { alpha: 2 } },
  },
  threads: {
    alpha: [
      {
        date: "2025-02-03",
        conversation: "sess-1",
        message_index: 3,
        score: 0.97,
        excerpt: "alpha excerpt",
      },
    ],
  },
  thread_overview: {
    schema_version: 1,
    total_threads: 1,
    threads: [
      {
        id: "alpha",
        label: "alpha",
        item_count: 1,
        conversation_count: 1,
        message_count: 1,
        palette_index: 0,
        top_examples: [
          {
            date: "2025-02-03",
            conversation: "sess-1",
            message_index: 3,
            score: 0.97,
            excerpt: "alpha excerpt",
          },
        ],
        conversation_breakdown: [
          {
            conversation: "sess-1",
            item_count: 1,
            message_count: 1,
            latest_date: "2025-02-03",
            avg_score: 0.97,
            preview_excerpt: "alpha excerpt",
          },
        ],
      },
    ],
  },
  metadata: {
    ui_hints: {
      infer_topics: true,
      k_selected: 1,
      k_option: "auto",
      preferred_k: 18,
      max_k: 40,
      coalesce_related: true,
      scope_mode: "folder",
      scope_folder: "events",
      top_n: 9,
      merged_label_count: 3,
    },
  },
};

const renderThreadsTab = (route = "/?tab=threads") =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <ThreadsTab />
    </MemoryRouter>,
  );

beforeAll(async () => {
  ThreadsTab = (await import("../ThreadsTab")).default;
});

describe("ThreadsTab", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/threads/summary") {
        return Promise.resolve({ data: { summary: summaryFixture } });
      }
      if (String(url).startsWith("/api/conversations/")) {
        return Promise.resolve({
          data: {
            messages: [
              { role: "user", text: "hello" },
              { role: "assistant", text: "alpha reply" },
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    vi.spyOn(axios, "post").mockImplementation((url) => {
      if (url === "/api/threads/search") {
        return Promise.resolve({ data: { matches: [] } });
      }
      if (url === "/api/threads/generate") {
        return Promise.resolve({ data: { summary: summaryFixture } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("loads the saved summary and renders the thread overview", async () => {
    renderThreadsTab();

    expect((await screen.findAllByRole("button", { name: /alpha/i })).length).toBeGreaterThan(0);
    expect(axios.get).toHaveBeenCalledWith("/api/threads/summary");
  });

  it("runs topic search when Enter is pressed in the search field", async () => {
    renderThreadsTab();

    const searchInput = await screen.findByPlaceholderText(/search by topic/i);
    fireEvent.change(searchInput, { target: { value: "alpha" } });
    fireEvent.keyDown(searchInput, { key: "Enter" });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/threads/search", {
        query: "alpha",
        top_k: 20,
      });
    });
  });

  it("shows empty filtered state when URL thread filter has no matches", async () => {
    renderThreadsTab("/?tab=threads&thread=missing");

    expect(await screen.findByText(/No threads match the active filter\./i)).toBeInTheDocument();
  });

  it("renders thread snippets and keeps focus when a snippet is opened", async () => {
    renderThreadsTab();

    const alphaButtons = await screen.findAllByRole("button", { name: /alpha/i });
    fireEvent.click(alphaButtons[0]);

    expect(await screen.findByRole("heading", { name: /Snippets/i })).toBeInTheDocument();
    fireEvent.click(await screen.findByText(/alpha excerpt/i));

    expect(screen.getByRole("button", { name: /deselect alpha/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Snippets/i })).toBeInTheDocument();
  });

  it("prefills generate options from the latest summary hints", async () => {
    renderThreadsTab();

    fireEvent.click(await screen.findByRole("button", { name: /generate options/i }));

    expect(await screen.findByLabelText(/top-k strategy/i)).toHaveValue("auto");
    expect(await screen.findByLabelText(/target k/i)).toHaveValue(18);
    expect(await screen.findByLabelText(/max k/i)).toHaveValue(40);
    expect(await screen.findByDisplayValue("folder")).toBeInTheDocument();
    expect(await screen.findByLabelText(/folder scope/i)).toHaveValue("events");
    expect(await screen.findByLabelText(/top threads to keep/i)).toHaveValue(9);
  });
});
