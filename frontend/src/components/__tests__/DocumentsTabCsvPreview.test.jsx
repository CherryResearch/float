import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("DocumentsTab CSV preview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders workspace csv files as a client-side table preview", async () => {
    const csvBody = [
      "destination,days,budget",
      "Paris,4,1200",
      "Kyoto,6,1800",
    ].join("\n");

    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/knowledge/list") {
        return Promise.resolve({
          data: {
            ids: ["doc-csv"],
            metadatas: [
              {
                source: "data/files/workspace/travel_itinerary.csv",
                relative_path: "workspace/travel_itinerary.csv",
                filename: "travel_itinerary.csv",
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
      if (url === "/api/knowledge/doc-csv") {
        return Promise.resolve({
          data: {
            documents: [csvBody],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(
      <MemoryRouter>
        <DocumentsTab />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /travel_itinerary\.csv/i }));

    expect(await screen.findByRole("checkbox", { name: /first row is header/i })).toBeChecked();
    expect(screen.getByText(/comma-delimited/i)).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /destination/i })).toBeInTheDocument();
    expect(screen.getByText("Paris")).toBeInTheDocument();
    expect(screen.getByText("Kyoto")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /first row is header/i }));

    await waitFor(() => {
      expect(screen.getByRole("columnheader", { name: /column 1/i })).toBeInTheDocument();
    });
    expect(screen.getAllByText("destination").length).toBeGreaterThan(0);
  });
});
