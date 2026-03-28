import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
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

vi.mock("../MediaViewer", () => ({
  default: () => <div data-testid="media-viewer" />,
}));

import DocumentsTab from "../DocumentsTab";

describe("DocumentsTab text editing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps markdown editable while leaving pdf rows and inspector view-only", async () => {
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/knowledge/list") {
        return Promise.resolve({
          data: {
            ids: ["doc-md", "doc-pdf"],
            metadatas: [
              {
                source: "data/files/workspace/meeting-notes.md",
                relative_path: "workspace/meeting-notes.md",
                filename: "meeting-notes.md",
                kind: "document",
              },
              {
                source: "data/files/workspace/manual.pdf",
                relative_path: "workspace/manual.pdf",
                filename: "manual.pdf",
                kind: "document",
              },
            ],
          },
        });
      }
      if (url === "/api/attachments") {
        return Promise.resolve({
          data: {
            attachments: [],
          },
        });
      }
      if (url === "/api/knowledge/doc-md") {
        return Promise.resolve({
          data: {
            documents: ["# Heading\n\nInitial markdown body."],
          },
        });
      }
      if (url === "/api/knowledge/doc-pdf") {
        return Promise.resolve({
          data: {
            documents: ["Extracted PDF text."],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    vi.spyOn(axios, "put").mockResolvedValue({ data: { status: "saved" } });

    render(
      <MemoryRouter>
        <DocumentsTab />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /meeting-notes\.md/i }));

    expect(await screen.findByText("Markdown document")).toBeInTheDocument();
    let inspector = screen.getByText("Markdown document").closest("section");
    fireEvent.click(screen.getByRole("button", { name: /edit markdown/i }));
    fireEvent.change(within(inspector).getByRole("textbox"), {
      target: { value: "# Updated heading\n\nAlpha" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save markdown/i }));
    });

    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith("/api/knowledge/doc-md", {
        text: "# Updated heading\n\nAlpha",
      });
    });

    await act(async () => {
      fireEvent.change(screen.getByDisplayValue("folders"), {
        target: { value: "list" },
      });
    });
    const listTable = screen.getByRole("table");
    const markdownRow = within(listTable).getByText("meeting-notes.md").closest("tr");
    const pdfRow = within(listTable).getByText("manual.pdf").closest("tr");
    expect(within(markdownRow).getByRole("button", { name: /edit text/i })).toBeInTheDocument();
    expect(within(pdfRow).queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(within(pdfRow).getByRole("button", { name: /inspect/i }));
    });

    inspector = screen.getByText("PDF document").closest("section");
    expect(within(inspector).getByText(/PDF files are view-only here\./i)).toBeInTheDocument();
    expect(within(inspector).queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
  });
});
