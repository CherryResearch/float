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

describe("DocumentsTab CSV editing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("supports in-app CSV table editing and persists the updated raw text", async () => {
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/knowledge/list") {
        return Promise.resolve({
          data: {
            ids: ["doc-csv"],
            metadatas: [
              {
                source: "data/files/workspace/metrics.csv",
                relative_path: "workspace/metrics.csv",
                filename: "metrics.csv",
                kind: "document",
              },
            ],
          },
        });
      }
      if (url === "/api/attachments") {
        return Promise.resolve({ data: { attachments: [] } });
      }
      if (url === "/api/knowledge/doc-csv") {
        return Promise.resolve({
          data: {
            documents: ["name,score\nalpha,1"],
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

    fireEvent.click(await screen.findByRole("button", { name: /metrics\.csv/i }));

    expect(await screen.findByText("Tabular document")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /edit csv/i }));

    const editorGroup = screen.getByRole("group", { name: /csv editor mode/i });
    expect(within(editorGroup).getByRole("button", { name: /table/i })).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("1"), {
      target: { value: "2" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /save csv/i }));
    });

    await waitFor(() => {
      expect(axios.put).toHaveBeenCalledWith("/api/knowledge/doc-csv", {
        text: "name,score\nalpha,2",
      });
    });
  });
});
