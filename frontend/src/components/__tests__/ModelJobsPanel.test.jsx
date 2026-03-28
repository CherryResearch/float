import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { vi } from "vitest";

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("axios", () => ({
  default: axiosMocks,
}));

import ModelJobsPanel from "../ModelJobsPanel";

describe("ModelJobsPanel", () => {
  beforeEach(() => {
    axiosMocks.get.mockReset();
    axiosMocks.post.mockReset();
  });

  it("renders model jobs and pauses a running job", async () => {
    axiosMocks.get
      .mockResolvedValueOnce({
        data: {
          jobs: [
            {
              id: "job-1",
              model: "gpt-oss-20b",
              status: "running",
              downloaded: 50,
              total: 100,
              percent: 0.5,
              path: "D:/models/gpt-oss-20b",
              updated_at: 1700000000,
            },
          ],
        },
      })
      .mockResolvedValueOnce({
        data: {
          jobs: [
            {
              id: "job-1",
              model: "gpt-oss-20b",
              status: "paused",
              downloaded: 50,
              total: 100,
              percent: 0.5,
              path: "D:/models/gpt-oss-20b",
              updated_at: 1700000001,
            },
          ],
        },
      });
    axiosMocks.post.mockResolvedValue({ data: { job: { id: "job-1", status: "paused" } } });

    render(<ModelJobsPanel />);

    expect(await screen.findByText("gpt-oss-20b")).toBeInTheDocument();
    expect(screen.getByText(/50%/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(axiosMocks.post).toHaveBeenCalledWith("/api/models/jobs/job-1/pause");
    });
    await waitFor(() => {
      expect(screen.getByText("paused")).toBeInTheDocument();
    });
  });
});
