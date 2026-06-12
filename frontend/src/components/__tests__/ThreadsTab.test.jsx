import React from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
      preferred_k: 8,
      max_k: 16,
      coalesce_related: true,
      scope_mode: "folder",
      scope_folder: "events",
      top_n: 16,
      merged_label_count: 3,
      embedding_model_requested: "sentence-transformers/all-MiniLM-L6-v2",
      sensitive_mode: true,
      topic_suggestion_provider_requested: "local",
      topic_suggestion_model_requested: "local:heuristic",
      topic_suggestion_provider: "local",
      topic_suggestion_model: "local:heuristic",
      suggested_topics: [{ topic: "planning", score: 1 }],
    },
    generation: {
      suggested_topics: [{ topic: "planning", score: 1 }],
      topic_seeds: [],
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
    window.localStorage.clear();
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
    vi.spyOn(axios, "post").mockImplementation((url, payload = {}) => {
      if (url === "/api/threads/search") {
        return Promise.resolve({ data: { matches: [] } });
      }
      if (url === "/api/threads/generate") {
        return Promise.resolve({
          data: {
            summary: {
              ...summaryFixture,
              metadata: {
                ...summaryFixture.metadata,
                ui_hints: {
                  ...summaryFixture.metadata.ui_hints,
                  topic_suggestion_provider_requested:
                    payload.topic_suggestion_provider
                    ?? summaryFixture.metadata.ui_hints.topic_suggestion_provider_requested,
                  topic_suggestion_model_requested:
                    payload.topic_suggestion_model
                    ?? summaryFixture.metadata.ui_hints.topic_suggestion_model_requested,
                  topic_suggestion_provider:
                    payload.topic_suggestion_provider
                    ?? summaryFixture.metadata.ui_hints.topic_suggestion_provider,
                  topic_suggestion_model:
                    payload.topic_suggestion_model
                    ?? summaryFixture.metadata.ui_hints.topic_suggestion_model,
                },
              },
            },
          },
        });
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
    const dialog = await screen.findByRole("dialog", { name: /thread generation options/i });
    fireEvent.click(within(dialog).getByText(/run mechanics and advanced controls/i));

    expect(await screen.findByLabelText(/top-k strategy/i)).toHaveValue("auto");
    expect(await screen.findByLabelText(/target k/i)).toHaveValue(8);
    expect(await screen.findByLabelText(/max k/i)).toHaveValue(16);
    expect(screen.getAllByDisplayValue("folder").length).toBeGreaterThan(0);
    expect(await screen.findByLabelText(/folder scope/i)).toHaveValue("events");
    expect(await screen.findByLabelText(/top threads to keep/i)).toHaveValue(16);
    expect(await screen.findByLabelText(/embedding model/i)).toHaveValue(
      "sentence-transformers/all-MiniLM-L6-v2",
    );
    expect(await screen.findByLabelText(/respect sensitive conversations/i)).toBeChecked();
    expect(screen.getAllByDisplayValue("local:heuristic").length).toBeGreaterThan(0);
  });

  it("uses recommended seeded topic tags for generation", async () => {
    renderThreadsTab();

    fireEvent.click(await screen.findByRole("button", { name: /generate options/i }));
    const dialog = await screen.findByRole("dialog", { name: /thread generation options/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /^recommended$/i }));

    expect((await screen.findAllByText("food")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("tool use").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /use topic tags/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/threads/generate",
        expect.objectContaining({
          infer_topics: false,
          embedding_model: "sentence-transformers/all-MiniLM-L6-v2",
          sensitive_mode: true,
          topic_suggestion_provider: "local",
          topic_suggestion_model: "local:heuristic",
          manual_threads: [
            "food",
            "philosophy",
            "tool use",
            "creative projects",
            "miscellaneous",
          ],
        }),
      );
    });
  });

  it("runs the high-level main-topic pass with compact auto-k defaults", async () => {
    renderThreadsTab();

    fireEvent.click(await screen.findByRole("button", { name: /generate options/i }));
    const inferButtons = screen.getAllByRole("button", { name: /infer main topics/i });
    fireEvent.click(inferButtons[inferButtons.length - 1]);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/threads/generate",
        expect.objectContaining({
          infer_topics: true,
          preferred_k: 8,
          max_k: 16,
          top_n: 16,
          manual_threads: [],
          topic_suggestion_provider: "local",
          topic_suggestion_model: "local:heuristic",
        }),
      );
    });
  });

  it("remembers separate local and api topic labeler choices", async () => {
    renderThreadsTab();

    fireEvent.click(await screen.findByRole("button", { name: /generate options/i }));
    const dialog = await screen.findByRole("dialog", { name: /thread generation options/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /^api$/i }));
    fireEvent.change(await within(dialog).findByLabelText(/primary api topic model/i), {
      target: { value: "gpt-4o-mini" },
    });
    const inferButtons = screen.getAllByRole("button", { name: /infer main topics/i });
    fireEvent.click(inferButtons[inferButtons.length - 1]);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/threads/generate",
        expect.objectContaining({
          topic_suggestion_provider: "api",
          topic_suggestion_model: "gpt-4o-mini",
        }),
      );
    });
    expect(window.localStorage.getItem("float:threads:topic-provider")).toBe("api");
    expect(window.localStorage.getItem("float:threads:topic-model:api")).toBe(
      "gpt-4o-mini",
    );
  });

  it("lets seed topics be added and removed as editable tags", async () => {
    renderThreadsTab();

    fireEvent.click(await screen.findByRole("button", { name: /generate options/i }));
    const dialog = await screen.findByRole("dialog", { name: /thread generation options/i });
    fireEvent.change(within(dialog).getByLabelText(/add topic seed/i), {
      target: { value: "food, philosophy" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^add typed topic seed$/i }));

    expect(await screen.findByRole("button", { name: /remove topic food/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /remove topic food/i }));

    const assignButtons = screen.getAllByRole("button", { name: /assign to seeds/i });
    fireEvent.click(assignButtons[assignButtons.length - 1]);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/threads/generate",
        expect.objectContaining({
          infer_topics: false,
          manual_threads: ["philosophy"],
        }),
      );
    });
  });
});
