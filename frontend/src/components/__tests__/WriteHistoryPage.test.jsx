import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
import { vi } from "vitest";
import WriteHistoryPage from "../WriteHistoryPage";

describe("WriteHistoryPage", () => {
  it("renders the standalone work history page and refresh button", () => {
    const onRefresh = vi.fn();
    const actions = [
      {
        id: "action-1",
        kind: "write",
        name: "write_file",
        summary: "Draft reply",
        status: "applied",
        created_at_ts: Date.parse("2026-03-24T23:38:00Z") / 1000,
        item_count: 1,
        revertible: true,
        response_id: "response-1",
        response_label: "response 1",
        conversation_id: "sess-123",
        conversation_label: "Current chat",
      },
    ];

    render(
      <MemoryRouter>
        <WriteHistoryPage
          actions={actions}
          backendReady
          loading={false}
          onRefresh={onRefresh}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: /work history/i })).toBeInTheDocument();
    expect(
      screen.getByText(/static view of the current reversible write-history cache/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/draft reply/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: /back to settings/i })).toHaveAttribute(
      "href",
      "/settings",
    );
  });
});
