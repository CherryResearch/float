import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";
import axios from "axios";
import WriteHistoryPage from "../WriteHistoryPage";

describe("WriteHistoryPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("separates runs from writes and expands metadata-only lifecycle details", async () => {
    const onRefresh = vi.fn();
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/work/runs/run-1/events") {
        return Promise.resolve({
          data: {
            events: [
              {
                sequence: 1,
                phase: "followup_pending",
                recorded_at: Date.parse("2026-07-25T07:59:00Z") / 1000,
                recovery_reason_code: "startup_resume",
              },
            ],
            count: 1,
          },
        });
      }
      if (url === "/api/work/runs/run-1/attempts") {
        return Promise.resolve({ data: { attempts: [], count: 0 } });
      }
      if (url === "/api/work/runs/run-1/effects") {
        return Promise.resolve({ data: { effects: [], count: 0 } });
      }
      return Promise.resolve({
        data: {
          runs: [
            {
              id: "run-1",
              event_id: "nightly-review",
              event_title: "Nightly review",
              action_name: "prompt",
              status: "prompted",
              phase: "complete",
              recovery_state: "terminal",
              recovery_count: 1,
              event_count: 1,
              summary: "Review found one actionable issue.",
              finished_at: Date.parse("2026-07-25T08:00:00Z") / 1000,
              occurrence_at: Date.parse("2026-07-25T08:00:00Z") / 1000,
              ownership: { conversation_id: "sess-123" },
            },
          ],
          count: 250,
        },
      });
    });
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
          userTimezone="America/Vancouver"
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: /activity/i })).toBeInTheDocument();
    expect(screen.getByText(/durable device-local ledger/i)).toBeInTheDocument();
    expect(await screen.findByText(/review found one actionable issue/i)).toBeInTheDocument();
    expect(screen.getByText(/^complete$/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /runs \(1 of 250\)/i })).toBeInTheDocument();
    expect(screen.getByText(/times use america\/vancouver/i)).toBeInTheDocument();
    expect(screen.getByText(/calendar nightly-review \/ chat sess-123/i)).toBeInTheDocument();
    expect(screen.getByText(/recovered once/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /show 1 transition/i }));
    expect(await screen.findByText(/followup pending/i)).toBeInTheDocument();
    expect(screen.getByText(/startup_resume/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /writes \(1\)/i }));
    expect(screen.getByText(/draft reply/i)).toBeInTheDocument();

    const refreshButton = screen.getByRole("button", { name: /^refresh$/i });
    fireEvent.click(refreshButton);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(axios.get).toHaveBeenCalledWith("/api/work/runs", {
      params: { limit: 100, offset: 0 },
    });
    expect(screen.getByRole("link", { name: /back to settings/i })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(screen.getByRole("link", { name: /open calendar/i })).toHaveClass(
      "work-history-page-action--primary",
    );
    expect(refreshButton).toHaveClass("work-history-page-action--quiet");
  });

  it("labels a checkpointed prompt resume as queued recovery", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        runs: [
          {
            id: "prompt-resume-receipt",
            event_id: "overnight-review",
            event_title: "Overnight review",
            action_name: "prompt",
            status: "prompt_resume_pending",
            recovery_state: "active",
            summary: "",
            ownership: {},
          },
        ],
        count: 1,
      },
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("Prompt recovery is queued from its durable checkpoint."),
    ).toBeInTheDocument();
    expect(screen.getByText(/prompt resume pending/i)).toBeInTheDocument();
  });

  it("shows retry and external-effect certainty without rendering raw payloads", async () => {
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/work/runs/run-retry/events") {
        return Promise.resolve({ data: { events: [], count: 0 } });
      }
      if (url === "/api/work/runs/run-retry/attempts") {
        return Promise.resolve({
          data: {
            attempts: [
              {
                id: "attempt-2",
                attempt_number: 2,
                provider: "openai",
                status: "retrying",
                error_category: "provider_timeout",
                retry_reason_code: "retry_after_provider_timeout",
                state_delta_certainty: "no_change_since_checkpoint",
                raw_prompt: "super-secret-prompt",
                raw_response: "super-secret-provider-response",
                error_message: "super-secret-error-detail",
              },
            ],
            count: 1,
          },
        });
      }
      if (url === "/api/work/runs/run-retry/effects") {
        return Promise.resolve({
          data: {
            effects: [
              {
                id: "effect-1",
                tool_name: "send_email",
                effect_scope: "external_email",
                status: "dispatched",
                certainty: "unknown",
                replay_policy: "reconcile_before_retry",
                raw_args: { recipient: "super-secret-recipient@example.com" },
                raw_result: "super-secret-tool-result",
              },
              {
                id: "effect-2",
                tool_name: "calendar_update",
                effect_scope: "external_calendar",
                status: "acknowledged",
                certainty: "reported_success",
                replay_policy: "never_auto_replay",
                permission_snapshot: { status: "declared", scopes: ["calendar.write"] },
                approval_snapshot: { required: true, status: "not_recorded" },
              },
            ],
            count: 2,
          },
        });
      }
      return Promise.resolve({
        data: {
          runs: [
            {
              id: "run-retry",
              event_title: "Provider recovery",
              action_name: "send_email",
              status: "running",
              event_count: 1,
              attempt_count: 1,
              effect_count: 2,
              summary: "Retry waiting for external-state reconciliation.",
              ownership: {},
            },
          ],
          count: 1,
        },
      });
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: /show 1 transition, 1 attempt, 2 effects/i,
      }),
    );

    const attempts = await screen.findByRole("region", { name: /provider attempts/i });
    expect(within(attempts).getByText(/provider attempt 2/i)).toBeInTheDocument();
    expect(within(attempts).getByText(/^provider timeout$/i)).toBeInTheDocument();
    expect(within(attempts).getByText(/retry after provider timeout/i)).toBeInTheDocument();
    expect(
      screen.getByText(/no other durable state changes were recorded/i),
    ).toBeInTheDocument();
    const effects = screen.getByRole("region", { name: /external effects/i });
    expect(within(effects).getByText("send_email")).toBeInTheDocument();
    expect(within(effects).getByText(/external email/i)).toBeInTheDocument();
    expect(within(effects).getByText(/reconcile before retry/i)).toBeInTheDocument();
    expect(
      screen.getByText(/state changes unknown; reconciliation required/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/external state was not independently verified/i),
    ).toBeInTheDocument();
    expect(within(effects).getByText(/^declared$/i)).toHaveAttribute(
      "title",
      expect.stringMatching(/server enforces its granted permission snapshot before dispatch/i),
    );
    expect(within(effects).getByText(/required \/ not recorded/i)).toBeInTheDocument();
    const reviewTab = screen.getByRole("tab", { name: /needs review \(1\)/i });
    expect(
      screen.getByRole("region", { name: /reconciliation for send_email/i }),
    ).toBeInTheDocument();
    fireEvent.click(reviewTab);
    expect(reviewTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Retry waiting for external-state reconciliation.")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /reconciliation for send_email/i }),
    ).toBeInTheDocument();

    expect(screen.queryByText(/super-secret/i)).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith("/api/work/runs/run-retry/attempts", {
      params: { limit: 100, offset: 0 },
    });
    expect(axios.get).toHaveBeenCalledWith("/api/work/runs/run-retry/effects", {
      params: { limit: 100, offset: 0 },
    });
  });

  it("reviews one scheduled authorization and refreshes after approving it once", async () => {
    const authorizationRun = {
      id: "run-authorization",
      event_id: "nightly-calendar-review",
      action_id: "action-send-summary",
      event_title: "Nightly calendar review",
      action_name: "calendar_update",
      status: "authorization_required",
      recovery_state: "attention",
      occurrence_at: Date.parse("2026-07-26T03:00:00Z") / 1000,
      authorization: {
        id: "authorization-1",
        request_digest: "sha256:scheduled-request",
        required_scopes: ["calendar.write", "network"],
        missing_scopes: ["calendar.write"],
        can_approve: true,
      },
      ownership: {},
    };
    const get = vi
      .spyOn(axios, "get")
      .mockResolvedValueOnce({
        data: {
          runs: [
            authorizationRun,
            {
              id: "run-complete",
              event_title: "Completed review",
              status: "complete",
              summary: "Already finished.",
              ownership: {},
            },
          ],
          count: 2,
        },
      })
      .mockResolvedValueOnce({ data: { runs: [], count: 0 } });
    const post = vi.spyOn(axios, "post").mockResolvedValue({
      data: { status: "approved" },
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("tab", { name: /needs review \(1\)/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /needs review \(1\)/i }));

    expect(screen.getByText(/approval required — nothing ran/i)).toBeInTheDocument();
    expect(
      screen.getByText(/waiting for authorization; no tool or external effect has run/i),
    ).toBeInTheDocument();
    expect(screen.getByText("calendar.write, network")).toBeInTheDocument();
    expect(screen.getByText("calendar.write")).toBeInTheDocument();
    expect(screen.getByText("Authorization needs attention")).toBeInTheDocument();
    expect(screen.queryByText(/^Needs reconciliation$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/already finished/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /approve and allow once/i }));

    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        "/api/calendar/events/nightly-calendar-review/actions/action-send-summary/authorization",
        {
          decision: "approve_once",
          authorization_id: "authorization-1",
          request_digest: "sha256:scheduled-request",
          occurrence_at: Date.parse("2026-07-26T03:00:00Z") / 1000,
        },
      );
    });
    expect(
      await screen.findByText(/no work needs authorization or effect confirmation/i),
    ).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("keeps a changed authorization unresolved and directs blocked approval to Calendar", async () => {
    const authorizationRun = {
      id: "run-blocked-authorization",
      event_id: "external-calendar-job",
      action_id: "action-external-write",
      event_title: "External calendar job",
      action_name: "calendar_update",
      status: "authorization_required",
      occurrence_at: Date.parse("2026-07-27T03:00:00Z") / 1000,
      authorization: {
        id: "authorization-blocked",
        request_digest: "sha256:blocked-request",
        required_scopes: ["calendar.write"],
        missing_scopes: ["calendar.write"],
        can_approve: false,
      },
      ownership: {},
    };
    vi.spyOn(axios, "get").mockResolvedValue({
      data: { runs: [authorizationRun], count: 1 },
    });
    const post = vi.spyOn(axios, "post").mockRejectedValue({
      response: {
        status: 409,
        data: { detail: "Authorization receipt no longer matches this occurrence." },
      },
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("tab", { name: /needs review \(1\)/i }));
    const review = screen.getByRole("region", {
      name: /authorization review for external calendar job/i,
    });
    expect(
      within(review).getByRole("button", { name: /approve and allow once/i }),
    ).toBeDisabled();
    expect(within(review).getByText(/cannot be approved from activity/i)).toBeInTheDocument();
    expect(within(review).getByRole("link", { name: /open calendar/i })).toHaveAttribute(
      "href",
      "/knowledge?tab=calendar&event_id=external-calendar-job",
    );

    fireEvent.click(within(review).getByRole("button", { name: /skip this occurrence/i }));

    const alert = await within(review).findByRole("alert");
    expect(alert).toHaveTextContent(/authorization receipt no longer matches/i);
    expect(alert).toHaveTextContent(/refresh activity and review the current state/i);
    expect(within(review).getByText(/approval required — nothing ran/i)).toBeInTheDocument();
    expect(
      within(review).getByRole("button", { name: /skip this occurrence/i }),
    ).not.toBeDisabled();
    expect(post).toHaveBeenCalledWith(
      "/api/calendar/events/external-calendar-job/actions/action-external-write/authorization",
      {
        decision: "deny",
        authorization_id: "authorization-blocked",
        request_digest: "sha256:blocked-request",
        occurrence_at: Date.parse("2026-07-27T03:00:00Z") / 1000,
      },
    );
  });

  it("records both user-verified reconciliation decisions without offering retry", async () => {
    const reconciliationRun = {
      id: "run-reconciliation",
      receipt_id: "receipt-reconciliation",
      event_title: "External delivery check",
      action_name: "send_email",
      status: "interrupted_unknown",
      effect_count: 2,
      summary: "External effects need review.",
      ownership: {},
    };
    const get = vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/work/runs/run-reconciliation/events") {
        return Promise.resolve({ data: { events: [], count: 0 } });
      }
      if (url === "/api/work/runs/run-reconciliation/attempts") {
        return Promise.resolve({ data: { attempts: [], count: 0 } });
      }
      if (url === "/api/work/runs/run-reconciliation/effects") {
        return Promise.resolve({
          data: {
            effects: [
              {
                id: "effect-applied",
                receipt_id: "receipt-reconciliation",
                tool_name: "send_email",
                status: "dispatched",
                certainty: "unknown",
                reconcile_required: true,
              },
              {
                id: "effect-no-change",
                receipt_id: "receipt-reconciliation",
                tool_name: "calendar_update",
                status: "unknown",
                certainty: "unknown",
              },
            ],
            count: 2,
          },
        });
      }
      return Promise.resolve({ data: { runs: [reconciliationRun], count: 1 } });
    });
    const post = vi
      .spyOn(axios, "post")
      .mockResolvedValueOnce({
        data: {
          warning: "Activity was reconciled, but its Calendar action is no longer available.",
          effect: {
            id: "effect-applied",
            status: "confirmed",
            certainty: "user_confirmed_applied",
            reconcile_required: false,
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          effect: {
            id: "effect-no-change",
            status: "confirmed",
            certainty: "user_confirmed_no_change",
            reconcile_required: false,
          },
        },
      });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByRole("tab", { name: /needs review \(1\)/i }),
    ).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: /show 2 effects/i }),
    );
    const appliedReview = await screen.findByRole("region", {
      name: /reconciliation for send_email/i,
    });
    const noChangeReview = screen.getByRole("region", {
      name: /reconciliation for calendar_update/i,
    });
    expect(within(appliedReview).getByText(/record only what you verified/i)).toBeInTheDocument();
    expect(within(appliedReview).queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();

    fireEvent.click(within(appliedReview).getByRole("button", { name: "It was applied" }));
    await waitFor(() => {
      expect(post).toHaveBeenNthCalledWith(
        1,
        "/api/work/runs/receipt-reconciliation/effects/effect-applied/reconcile",
        { decision: "confirm_applied" },
      );
      expect(
        screen.queryByRole("region", { name: /reconciliation for send_email/i }),
      ).not.toBeInTheDocument();
    });
    expect(
      screen.getByText(
        "Activity was reconciled, but its Calendar action is no longer available.",
      ),
    ).toHaveAttribute("role", "status");

    fireEvent.click(
      within(noChangeReview).getByRole("button", { name: "No change happened" }),
    );
    await waitFor(() => {
      expect(post).toHaveBeenNthCalledWith(
        2,
        "/api/work/runs/receipt-reconciliation/effects/effect-no-change/reconcile",
        { decision: "confirm_no_change" },
      );
      expect(
        screen.queryByRole("region", { name: /reconciliation for calendar_update/i }),
      ).not.toBeInTheDocument();
    });
    expect(get.mock.calls.filter(([url]) => url === "/api/work/runs")).toHaveLength(3);
  });

  it("keeps a reconciliation conflict visible and unresolved", async () => {
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/work/runs/run-reconciliation-conflict/events") {
        return Promise.resolve({ data: { events: [], count: 0 } });
      }
      if (url === "/api/work/runs/run-reconciliation-conflict/attempts") {
        return Promise.resolve({ data: { attempts: [], count: 0 } });
      }
      if (url === "/api/work/runs/run-reconciliation-conflict/effects") {
        return Promise.resolve({
          data: {
            effects: [
              {
                id: "effect-conflict",
                receipt_id: "receipt-conflict",
                tool_name: "calendar_update",
                status: "acknowledged",
                certainty: "reported_success",
                reconcile_required: true,
              },
            ],
            count: 1,
          },
        });
      }
      return Promise.resolve({
        data: {
          runs: [
            {
              id: "run-reconciliation-conflict",
              receipt_id: "receipt-conflict",
              event_title: "Changed elsewhere",
              status: "running",
              effect_count: 1,
              ownership: {},
            },
          ],
          count: 1,
        },
      });
    });
    const post = vi.spyOn(axios, "post").mockRejectedValue({
      response: {
        status: 409,
        data: { detail: "Effect evidence no longer accepts this decision." },
      },
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /show 1 effect/i }));
    const review = await screen.findByRole("region", {
      name: /reconciliation for calendar_update/i,
    });
    fireEvent.click(within(review).getByRole("button", { name: "No change happened" }));

    const alert = await within(review).findByRole("alert");
    expect(alert).toHaveTextContent(/effect evidence no longer accepts this decision/i);
    expect(alert).toHaveTextContent(/refresh activity and review the current evidence/i);
    expect(within(review).getByRole("button", { name: "It was applied" })).not.toBeDisabled();
    expect(
      within(review).getByRole("button", { name: "No change happened" }),
    ).not.toBeDisabled();
    expect(within(review).queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledWith(
      "/api/work/runs/receipt-conflict/effects/effect-conflict/reconcile",
      { decision: "confirm_no_change" },
    );
  });

  it("requests a cooperative stop for active Calendar work and refreshes its state", async () => {
    const activeRun = {
      id: "receipt-active-stop",
      run_id: "run-active-stop",
      event_id: "nightly-calendar-job",
      action_id: "action-calendar-write",
      event_title: "Nightly calendar job",
      action_name: "calendar_update",
      status: "followup_running",
      summary: "Calendar follow-up is running.",
      ownership: {},
    };
    let runRequestCount = 0;
    const get = vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/work/runs") {
        runRequestCount += 1;
        return Promise.resolve({
          data: {
            runs: [
              runRequestCount === 1
                ? activeRun
                : { ...activeRun, status: "cancel_requested", cancel_requested: true },
            ],
            count: 1,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const post = vi.spyOn(axios, "post").mockResolvedValue({
      data: {
        status: "cancel_requested",
        warning: "Cancellation was saved, but Calendar search needs refresh.",
      },
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    const stopRegion = await screen.findByRole("region", {
      name: /stop request for nightly calendar job/i,
    });
    const stopButton = within(stopRegion).getByRole("button", { name: "Request stop" });
    expect(stopButton.getAttribute("title")).toMatch(/stop before dispatch when possible/i);
    expect(stopButton.getAttribute("title")).toMatch(
      /already-dispatched non-cooperative work may still finish and require reconciliation/i,
    );
    expect(stopRegion).toHaveTextContent(/stop before dispatch when possible/i);
    expect(stopRegion).toHaveTextContent(
      /already-dispatched non-cooperative work may still finish and require reconciliation/i,
    );
    expect(stopRegion).not.toHaveTextContent(/kill|terminate/i);

    fireEvent.click(stopButton);

    await waitFor(() => {
      expect(post).toHaveBeenCalledWith(
        "/api/calendar/events/nightly-calendar-job/actions/action-calendar-write/cancel",
        { run_id: "run-active-stop" },
      );
      expect(within(stopRegion).getByText("Stop requested")).toHaveAttribute(
        "role",
        "status",
      );
    });
    expect(within(stopRegion).queryByRole("button", { name: "Request stop" })).not.toBeInTheDocument();
    expect(
      within(stopRegion).getByText(
        "Cancellation was saved, but Calendar search needs refresh.",
      ),
    ).toHaveAttribute("role", "status");
    expect(get.mock.calls.filter(([url]) => url === "/api/work/runs")).toHaveLength(2);
  });

  it("keeps stop conflicts inline and excludes terminal or reconciliation work", async () => {
    const runs = [
      {
        id: "receipt-stop-conflict",
        run_id: "run-stop-conflict",
        event_id: "calendar-sync",
        action_id: "sync-action",
        event_title: "Calendar sync",
        status: "running",
        summary: "Ordinary active Calendar work.",
        ownership: {},
      },
      {
        id: "receipt-complete",
        run_id: "run-complete",
        event_id: "completed-calendar-job",
        action_id: "completed-action",
        event_title: "Completed Calendar work",
        status: "complete",
        summary: "Terminal work stays terminal.",
        ownership: {},
      },
      {
        id: "receipt-reconciliation",
        run_id: "run-reconciliation-active",
        event_id: "external-delivery",
        action_id: "delivery-action",
        event_title: "External delivery",
        status: "running",
        reconcile_required: true,
        summary: "External effect requires reconciliation.",
        ownership: {},
      },
      {
        id: "receipt-missing-run-id",
        event_id: "missing-run-id",
        action_id: "missing-run-action",
        event_title: "Incomplete active receipt",
        status: "running",
        summary: "Active evidence is incomplete.",
        ownership: {},
      },
    ];
    vi.spyOn(axios, "get").mockResolvedValue({ data: { runs, count: runs.length } });
    const post = vi.spyOn(axios, "post").mockRejectedValue({
      response: {
        status: 409,
        data: { detail: "This run already moved to another state." },
      },
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    const reviewTab = await screen.findByRole("tab", { name: /needs review \(1\)/i });
    const stopButtons = screen.getAllByRole("button", { name: "Request stop" });
    expect(stopButtons).toHaveLength(1);
    const stopRegion = screen.getByRole("region", { name: /stop request for calendar sync/i });
    expect(stopRegion).toHaveTextContent(/stop before dispatch when possible/i);
    expect(stopRegion).toHaveTextContent(/non-cooperative work may still finish/i);
    expect(stopRegion).not.toHaveTextContent(/kill|terminate/i);

    fireEvent.click(stopButtons[0]);

    const alert = await within(stopRegion).findByRole("alert");
    expect(alert).toHaveTextContent(/this run already moved to another state/i);
    expect(alert).toHaveTextContent(/refresh activity and review the current state/i);
    expect(within(stopRegion).getByRole("button", { name: "Request stop" })).not.toBeDisabled();
    expect(post).toHaveBeenCalledWith(
      "/api/calendar/events/calendar-sync/actions/sync-action/cancel",
      { run_id: "run-stop-conflict" },
    );

    fireEvent.click(reviewTab);
    expect(screen.getByText(/review work that needs your confirmation/i)).toBeInTheDocument();
    expect(screen.getByText(/paused before dispatch/i)).toBeInTheDocument();
    expect(screen.getByText(/record what you verified after dispatch/i)).toBeInTheDocument();
    expect(screen.getByText("External effect requires reconciliation.")).toBeInTheDocument();
    expect(screen.queryByText("Ordinary active Calendar work.")).not.toBeInTheDocument();
    expect(screen.queryByText("Terminal work stays terminal.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request stop" })).not.toBeInTheDocument();
  });

  it("keeps partial evidence visible, reports truncation, and clears cached details", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/work/runs/run-partial/events") {
        return Promise.reject(new Error("private lifecycle failure"));
      }
      if (url === "/api/work/runs/run-partial/attempts") {
        return Promise.resolve({
          data: {
            attempts: [
              {
                id: "attempt-1",
                attempt_number: 1,
                status: "retry_scheduled",
                error_category: "provider_timeout",
                retry_reason_code: "provider_timeout",
                state_delta_certainty: "no_change_since_checkpoint",
              },
            ],
            count: 125,
            has_more: true,
            next_offset: 100,
          },
        });
      }
      if (url === "/api/work/runs/run-partial/effects") {
        return Promise.resolve({
          data: {
            effects: [
              {
                id: "effect-1",
                tool_name: "calendar_update",
                effect_scope: "external_calendar",
                status: "unknown",
                certainty: "unknown",
                replay_policy: "never_auto_replay",
              },
            ],
            count: 1,
          },
        });
      }
      return Promise.resolve({
        data: {
          runs: [
            {
              id: "run-partial",
              event_title: "Partial evidence",
              status: "running",
              event_count: 1,
              attempt_count: 125,
              effect_count: 1,
              ownership: {},
            },
            {
              id: "run-failed",
              event_title: "Failed without summary",
              status: "failed",
              ownership: {},
            },
          ],
          count: 2,
        },
      });
    });

    render(
      <MemoryRouter>
        <WriteHistoryPage backendReady loading={false} />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/run is still in progress; no text summary/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/run ended without a text summary; inspect its evidence/i),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: /show 1 transition, 125 attempts, 1 effect/i,
      }),
    );

    expect(
      await screen.findByText(/lifecycle transitions are unavailable right now/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/provider attempt 1/i)).toBeInTheDocument();
    expect(screen.getByText(/showing first 1 of 125 attempts/i)).toBeInTheDocument();
    expect(screen.getByText("calendar_update")).toBeInTheDocument();
    expect(
      screen.getByText(/state changes unknown; reconciliation required/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^refresh$/i }));
    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: /show 1 transition, 125 attempts, 1 effect/i,
        }),
      ).not.toBeDisabled();
    });
    expect(screen.queryByText(/provider attempt 1/i)).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: /show 1 transition, 125 attempts, 1 effect/i,
      }),
    );
    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledTimes(2 + 3 * 2);
    });
  });
});
