import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import axios from "axios";

vi.mock("../FilterBar", () => ({
  default: ({ children, onSearch, right, searchPlaceholder, searchValue }) => (
    <div>
      <input
        aria-label="Filter memory"
        placeholder={searchPlaceholder}
        value={searchValue}
        onChange={(event) => onSearch(event.target.value)}
      />
      {right}
      {children}
    </div>
  ),
}));

import MemoryTab from "../MemoryTab";

describe("MemoryTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  it("reloads with archived items when requested and surfaces last accessed details", async () => {
    const activeItem = {
      key: "active-note",
      value: "active value",
      importance: 1,
      created_at: 1710000000,
      updated_at: 1710001800,
      last_accessed_at: 1710002400,
      sensitivity: "mundane",
      vectorize: false,
    };
    const archivedItem = {
      key: "archived-note",
      value: "archived value",
      importance: 1,
      created_at: 1710000000,
      updated_at: 1710003000,
      last_accessed_at: 1710003600,
      sensitivity: "mundane",
      pruned_at: 1710004200,
      vectorize: false,
    };

    vi.spyOn(axios, "get").mockImplementation((url, config = {}) => {
      if (url === "/api/memory") {
        const includeArchived = Boolean(config?.params?.include_archived);
        return Promise.resolve({
          data: {
            items: includeArchived ? [activeItem, archivedItem] : [activeItem],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<MemoryTab />);

    expect(await screen.findByText("active-note")).toBeInTheDocument();
    expect(screen.queryByText("archived-note")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /show archived/i }));

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith("/api/memory", {
        params: {
          detailed: true,
          include_archived: true,
        },
      });
    });

    fireEvent.click(await screen.findByText("archived-note"));

    expect(screen.getByRole("button", { name: /unarchive/i })).toBeInTheDocument();
    expect(
      screen.getByText(new Date(archivedItem.last_accessed_at * 1000).toLocaleString()),
    ).toBeInTheDocument();
  });
});
