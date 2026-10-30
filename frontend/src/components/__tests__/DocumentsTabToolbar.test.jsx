import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";
import axios from "axios";
import DocumentsTab from "../DocumentsTab";

vi.mock("../MediaViewer", () => ({ default: () => null }));

describe("DocumentsTab toolbar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/knowledge/list") {
        return Promise.resolve({ data: {
          ids: ["a", "z"],
          metadatas: [
            { title: "Alpha note", source: "data/files/workspace/alpha.md", relative_path: "workspace/alpha.md" },
            { title: "Zebra note", source: "data/files/workspace/zebra.md", relative_path: "workspace/zebra.md", archived: true },
          ],
        } });
      }
      if (url === "/api/attachments") return Promise.resolve({ data: { attachments: [] } });
      if (url === "/api/knowledge/query") return Promise.resolve({ data: { matches: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  const renderTab = () => render(<MemoryRouter><DocumentsTab /></MemoryRouter>);

  it("keeps the active settings visible and applies search, archive, and sort with Filters closed", async () => {
    renderTab();
    await screen.findByRole("button", { name: /Alpha note/ });
    const filters = screen.getByText("Filters").closest("details");
    expect(filters).not.toHaveAttribute("open");
    expect(screen.getByLabelText("Search documents")).toBeVisible();

    fireEvent.click(screen.getByText("Filters"));
    fireEvent.change(screen.getByLabelText("Layout"), { target: { value: "list" } });
    fireEvent.change(screen.getByLabelText("Sort documents"), { target: { value: "title:desc" } });
    fireEvent.click(screen.getByLabelText("Archived documents"));
    fireEvent.click(screen.getByText("Filters"));

    expect(filters).not.toHaveAttribute("open");
    expect(filters.querySelector("summary")).toHaveTextContent("Title, Z to A · Archived documents");
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Zebra note");
    expect(rows[2]).toHaveTextContent("Alpha note");

    fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "Alpha" } });
    expect(screen.queryByText("Zebra note")).not.toBeInTheDocument();
    expect(screen.getByText("Alpha note")).toBeVisible();

    fireEvent.click(screen.getByText("Filters"));
    expect(screen.getByLabelText("Archived documents")).toBeChecked();
    expect(screen.getByLabelText("Sort documents")).toHaveValue("title:desc");
  });

  it("preserves image retrieval parameters behind clearly labelled search controls", async () => {
    renderTab();
    fireEvent.click(screen.getByText("Filters"));
    fireEvent.change(screen.getByLabelText("Search method"), { target: { value: "semantic" } });
    fireEvent.change(screen.getByLabelText("Search content"), { target: { value: "clip" } });
    fireEvent.click(screen.getByText("Filters"));
    fireEvent.change(screen.getByLabelText("Search documents"), { target: { value: "sunset over water" } });

    await waitFor(() => expect(axios.get).toHaveBeenCalledWith("/api/knowledge/query", {
      params: { q: "sunset over water", k: 8, mode: "clip" },
    }));
    expect(screen.getByText("Filters").closest("summary")).toHaveTextContent("Meaning and images · Images");
  });

  it("opens the chosen add flow and dismisses the menu with Escape or an outside press", async () => {
    renderTab();
    const add = screen.getByText("Add").closest("summary");
    const openAdd = () => {
      fireEvent.click(add);
      // Native toggle is dispatched after the browser applies the open state.
      fireEvent(add.closest("details"), new Event("toggle"));
    };
    openAdd();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(add.closest("details")).not.toHaveAttribute("open"));
    expect(add).toHaveFocus();

    openAdd();
    fireEvent.pointerDown(screen.getByLabelText("Search documents"));
    await waitFor(() => expect(add.closest("details")).not.toHaveAttribute("open"));

    openAdd();
    fireEvent.click(screen.getByRole("button", { name: /Quick note/ }));
    expect(screen.getByRole("heading", { name: "Quick note" })).toBeVisible();
    expect(add.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
