import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";

vi.mock("../FilterBar", () => ({
  default: ({ children, onSearch, searchPlaceholder, searchValue }) => (
    <div>
      <input
        aria-label="Search documents"
        placeholder={searchPlaceholder}
        value={searchValue}
        onChange={(event) => onSearch(event.target.value)}
      />
      {children}
    </div>
  ),
}));

import DocumentsTab from "../DocumentsTab";

const attachment = {
  content_hash: "hash-owl",
  filename: "IMG_2042.png",
  display_name: "",
  folder: "",
  source_url: "",
  source_url_recorded_at: "",
  content_type: "image/png",
  size: 128000,
  uploaded_at: "2026-07-29T18:00:00Z",
  url: "/api/attachments/hash-owl/IMG_2042.png",
  relative_path: "uploads/hash-owl/IMG_2042.png",
  origin: "upload",
  caption: "Image attachment without generated caption.",
  caption_status: "placeholder",
  placeholder_caption: true,
  index_status: "indexed",
};

describe("DocumentsTab media actions", () => {
  let currentAttachment;

  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    currentAttachment = { ...attachment };
    vi.spyOn(window, "open").mockReturnValue(null);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/knowledge/list") {
        return Promise.resolve({ data: { ids: [], metadatas: [] } });
      }
      if (url === "/api/attachments") {
        return Promise.resolve({ data: { attachments: [currentAttachment] } });
      }
      if (url === "/api/attachments/caption/hash-owl") {
        return Promise.resolve({ data: currentAttachment });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    vi.spyOn(axios, "patch").mockImplementation((_url, updates) => {
      currentAttachment = {
        ...currentAttachment,
        ...updates,
        source_url_recorded_at: updates.source_url
          ? "2026-07-29T18:05:00Z"
          : currentAttachment.source_url_recorded_at,
      };
      return Promise.resolve({
        data: {
          status: "saved",
          attachment: currentAttachment,
        },
      });
    });
    vi.spyOn(axios, "delete").mockResolvedValue({
      data: { status: "deleted", content_hash: "hash-owl" },
    });
  });

  const renderTab = () =>
    render(
      <MemoryRouter>
        <DocumentsTab />
      </MemoryRouter>,
    );

  const openActions = async () => {
    const summary = await screen.findByText("actions");
    fireEvent.click(summary);
    return summary.closest("details");
  };

  it("offers keyboard/touch friendly rename, folder, source, caption, and delete actions", async () => {
    renderTab();

    expect(
      await screen.findByRole("button", {
        name: /caption unavailable.*clip image retrieval is indexed separately/i,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("caption unavailable")).toHaveLength(1);

    let menu = await openActions();
    fireEvent.click(within(menu).getByRole("button", { name: /rename display label/i }));
    const renameForm = screen.getByRole("form", { name: /rename img_2042\.png/i });
    fireEvent.change(within(renameForm).getByRole("textbox"), {
      target: { value: "Ravine owl" },
    });
    fireEvent.click(within(renameForm).getByRole("button", { name: "save" }));

    await waitFor(() => {
      expect(axios.patch).toHaveBeenCalledWith(
        "/api/attachments/hash-owl/metadata",
        { display_name: "Ravine owl" },
      );
    });
    expect(await screen.findByText("Ravine owl")).toBeInTheDocument();
    expect(screen.getByText(/file IMG_2042\.png/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/new gallery folder/i), {
      target: { value: "Wildlife" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add folder/i }));
    fireEvent.click(screen.getByRole("button", { name: /^all\s*1$/i }));

    menu = await openActions();
    fireEvent.click(within(menu).getByRole("button", { name: /move to folder/i }));
    const moveForm = screen.getByRole("form", { name: /move ravine owl/i });
    fireEvent.change(within(moveForm).getByRole("combobox"), {
      target: { value: "Wildlife" },
    });
    fireEvent.click(within(moveForm).getByRole("button", { name: "save" }));
    await waitFor(() => {
      expect(axios.patch).toHaveBeenCalledWith(
        "/api/attachments/hash-owl/metadata",
        { folder: "Wildlife" },
      );
    });

    menu = await openActions();
    fireEvent.click(within(menu).getByRole("button", { name: /edit source provenance/i }));
    const sourceForm = screen.getByRole("form", { name: /source provenance for ravine owl/i });
    fireEvent.change(within(sourceForm).getByRole("textbox"), {
      target: { value: "https://example.com/owl-observation" },
    });
    fireEvent.click(within(sourceForm).getByRole("button", { name: "save" }));
    await waitFor(() => {
      expect(axios.patch).toHaveBeenCalledWith(
        "/api/attachments/hash-owl/metadata",
        { source_url: "https://example.com/owl-observation" },
      );
    });
    expect(await screen.findByRole("link", { name: /recorded source/i })).toHaveAttribute(
      "href",
      "https://example.com/owl-observation",
    );
    expect(screen.getByRole("link", { name: /float copy/i })).toHaveAttribute(
      "href",
      "/api/attachments/hash-owl/IMG_2042.png",
    );

    menu = await openActions();
    fireEvent.click(within(menu).getByRole("button", { name: /edit caption/i }));
    expect(await screen.findByRole("textbox", { name: /image caption/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /close viewer/i }));

    menu = await openActions();
    fireEvent.click(
      within(menu).getByRole("button", { name: /delete attachment \+ retrieval/i }),
    );
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/stored file, its caption, and its retrieval records/i),
    );
    await waitFor(() => {
      expect(axios.delete).toHaveBeenCalledWith("/api/attachments/hash-owl");
    });
    expect(await screen.findByText(/No uploads yet/i)).toBeInTheDocument();
  });

  it("requires action-local consent before a bulk cloud caption refresh", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/knowledge/list") {
        return Promise.resolve({ data: { ids: [], metadatas: [] } });
      }
      if (url === "/api/attachments") {
        return Promise.resolve({ data: { attachments: [currentAttachment] } });
      }
      if (url === "/api/attachments/caption/status") {
        return Promise.resolve({ data: { engine: "cloud", ready: true } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    const postSpy = vi.spyOn(axios, "post").mockResolvedValue({
      data: { scanned: 1, reindexed: 1 },
    });
    window.confirm.mockReturnValueOnce(false);
    renderTab();

    fireEvent.click(await screen.findByRole("button", { name: /refresh image index/i }));

    await waitFor(() => {
      expect(window.confirm).toHaveBeenCalledWith(
        expect.stringMatching(/saved caption setting is Cloud\/provider.*image bytes may be sent/i),
      );
    });
    expect(postSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(/Refresh cancelled; no images were sent/i)).toBeInTheDocument();

    window.confirm.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: /refresh image index/i }));
    await waitFor(() => {
      expect(postSpy).toHaveBeenCalledWith("/api/attachments/rag/rehydrate", {
        dry_run: false,
      });
    });
    expect(await screen.findByText(/refreshed 1 image/i)).toBeInTheDocument();
  });

  it("keeps partial deletion cleanup visible and retryable", async () => {
    axios.delete
      .mockRejectedValueOnce({
        response: {
          data: {
            detail: {
              status: "partial",
              message: "Attachment content was deleted, but cleanup was incomplete",
              content_deleted: true,
              cleanup_failed: ["knowledge"],
            },
          },
        },
      })
      .mockResolvedValueOnce({
        data: { status: "deleted", content_hash: "hash-owl" },
      });
    renderTab();

    let menu = await openActions();
    fireEvent.click(
      within(menu).getByRole("button", { name: /delete attachment \+ retrieval/i }),
    );

    expect(await screen.findByText(/Stored file removed/i)).toBeInTheDocument();
    expect(screen.getByText(/Cleanup still needs: knowledge/i)).toBeInTheDocument();
    expect(screen.getByText(/cleanup was incomplete.*retry cleanup/i)).toBeInTheDocument();

    menu = await openActions();
    expect(within(menu).queryByRole("button", { name: /open viewer/i }))
      .not.toBeInTheDocument();
    fireEvent.click(within(menu).getByRole("button", { name: /retry cleanup/i }));
    expect(window.confirm).toHaveBeenLastCalledWith(
      expect.stringMatching(/stored file is already gone/i),
    );

    expect(await screen.findByText(/No uploads yet/i)).toBeInTheDocument();
  });

  it("treats an explicitly empty backend folder as authoritative", async () => {
    localStorage.setItem(
      "documentsAttachmentFolderAssignments",
      JSON.stringify({ "hash-owl": "Stale browser folder" }),
    );
    renderTab();

    expect(await screen.findByRole("button", { name: /^unsorted\s*1$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stale browser folder/i })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(
        JSON.parse(localStorage.getItem("documentsAttachmentFolderAssignments") || "{}"),
      ).toEqual({});
    });
  });

  it("keeps the browser folder fallback when an older backend omits folder metadata", async () => {
    delete currentAttachment.folder;
    localStorage.setItem(
      "documentsAttachmentFolderAssignments",
      JSON.stringify({ "hash-owl": "Legacy local folder" }),
    );
    renderTab();

    expect(await screen.findByRole("button", { name: /^legacy local folder\s*1$/i }))
      .toBeInTheDocument();
    expect(
      JSON.parse(localStorage.getItem("documentsAttachmentFolderAssignments") || "{}"),
    ).toEqual({ "hash-owl": "Legacy local folder" });
  });
});
