import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "axios";

import KnowledgeSyncTab from "../KnowledgeSyncTab";

expect.extend(matchers);

const buildOverview = () => ({
  deployment_status: {
    schema_version: 1,
    software: {
      release_version: "0.1.0a1",
      build_code: "b12",
      label: "0.1.0a1 // b12",
      state: "built",
      snapshot_digest: "sha256:test-snapshot",
    },
    data: {
      deployment_id: "deployment-studio-1234",
      display_name: "Studio",
      state: "synced",
      workspace_count: 1,
      last_updated_at: "2026-07-16T10:00:00+00:00",
      revision: {
        digest: "a".repeat(64),
        code: "d-studio123456",
        observed_at_iso: "2026-07-16T10:00:00+00:00",
      },
      sync_checkpoint: {
        state: "synced",
        summary: "Local data matches the last successful sync checkpoint",
        peer_deployment_id: "deployment-pear-1234",
        peer_label: "Pear",
        last_synced_at: 1784217600,
      },
    },
  },
  current_device: {
    deployment_id: "deployment-studio-1234",
    display_name: "Studio",
    hostname: "studio-host",
    source_namespace: "Studio",
    software: {
      release_version: "0.1.0a1",
      build_code: "b12",
      label: "0.1.0a1 // b12",
    },
  },
  device_access: {
    visibility: {
      lan_enabled: true,
      lan_listening: true,
      lan_binding_known: true,
      lan_bind_host: "0.0.0.0",
      lan_state: "listening",
    },
    listener: {
      bind_host: "0.0.0.0",
      binding_known: true,
      lan_listening: true,
      launcher_running: true,
      restart_supported: true,
    },
    advertised_urls: {
      lan: "http://studio.local:59185",
      lan_candidate: "http://studio.local:59185",
      local: "http://127.0.0.1:59185",
    },
  },
  sync_defaults: {
    remote_url: "",
    visible_on_lan: true,
    auto_accept_push: false,
    link_to_source: false,
    source_namespace: "Studio",
    saved_peers: [],
  },
  egress_summary: {
    private_network_only: true,
    inbound_visibility: {
      lan_enabled: true,
      online_requested: false,
      online_supported: false,
    },
    outbound_target: {
      mode: "none",
      remote_url: "",
      peer_id: "",
      peer_label: "",
    },
    push_review_mode: "review_required",
    saved_peer_count: 0,
    background_owner: {
      mode: "idle",
    },
    auto_sync: {
      enabled: false,
      available: false,
      mode: "manual_review_only",
      reason:
        "Automatic sync is not enabled. This device can suggest manual Check remote and Preview sync steps, but it will not schedule or apply sync automatically.",
    },
    unfinished_notice:
      "Automatic stop-kill safeguards are future work. Stop records cancel intent and aborts the current local request where possible, but it does not kill remote work that another device already accepted.",
  },
  sync_operations: {
    active_operation: null,
    last_attempt: null,
    recent: [],
  },
  sync_suggestions: [],
  workspaces: {
    profiles: [
      {
        id: "root",
        name: "Main workspace",
        slug: "main",
        namespace: "",
        root_path: "data/files/workspace",
        kind: "root",
        is_root: true,
      },
    ],
    active_workspace_id: "root",
    selected_workspace_ids: ["root"],
  },
  inbound_devices: [],
  legacy_inbound_devices: [],
  sync_reviews: {
    pending: [],
    recent: [],
  },
  device_counts: {
    paired: 0,
    trusted: 0,
    legacy: 0,
    pending_push_reviews: 0,
  },
});

const buildPairedOverview = (remoteUrl = "http://peer.float:5000") => {
  const overview = buildOverview();
  const peer = {
    id: "peer-pear",
    label: "Pear",
    remote_url: remoteUrl,
    scopes: ["sync"],
    remote_device_id: "remote-device-1",
    remote_public_key: "pk-pear",
    remote_device_name: "Pear",
    remote_deployment_id: "deployment-pear-1234",
    remote_software: {
      release_version: "0.1.0a1",
      build_code: "b11",
      label: "0.1.0a1 // b11",
      snapshot_digest: "sha256:pear-snapshot",
    },
    remote_data: {
      deployment_id: "deployment-pear-1234",
      display_name: "Pear",
      state: "ready",
      workspace_count: 1,
      revision: {
        digest: "b".repeat(64),
        code: "d-pear12345678",
      },
    },
    data_checkpoint: {
      state: "local_changes",
      summary: "Local data changed after the last successful sync",
      peer_deployment_id: "deployment-pear-1234",
      peer_label: "Pear",
      last_synced_at: 1784217600,
    },
    last_status_at: "2026-07-15T12:00:00+00:00",
    local_workspace_ids: ["root"],
    remote_workspace_ids: ["root"],
    workspace_mode: "merge",
    local_target_workspace_id: "root",
    remote_target_workspace_id: "root",
  };
  overview.sync_defaults.remote_url = remoteUrl;
  overview.sync_defaults.saved_peers = [peer];
  overview.device_counts.paired = 1;
  return overview;
};

const buildMobileServeStatus = (overrides = {}) => ({
  ok: true,
  installed: true,
  running: false,
  serve_port: 64345,
  frontend_port: 50802,
  backend_port: 50801,
  tailnet_host: "studio.tail.ts.net",
  url: "http://studio.tail.ts.net:64345/",
  target: "localhost:50802",
  status_text: "ready",
  warning: "Tailscale Serve is not currently pointing at this Float frontend.",
  ...overrides,
});

const buildPlanResponse = () => ({
  plan_receipt: "receipt-token",
  remote: {
    display_name: "Pear",
    hostname: "pear-host",
    base_url: "http://peer.float:5000",
  },
  workspace_mode: "merge",
  workspaces: {
    local: { target_workspace_id: "root", ignored_workspace_ids: [] },
    remote: {
      target_workspace_id: "root",
      profiles: [
        {
          id: "root",
          name: "Main workspace",
          slug: "main",
          namespace: "",
          root_path: "data/files/workspace",
          kind: "root",
          is_root: true,
        },
      ],
    },
  },
  pull_sections: [
    {
      key: "conversations",
      label: "Conversations",
      only_remote: 1,
      remote_newer: 1,
      only_local: 1,
      local_newer: 0,
      identical: 4,
      change_count: 3,
      selected_by_default: true,
      items: [
        {
          resource_id: "conv-a",
          selection_id: "conv-a",
          label: "Alpha",
          detail: "pear/alpha | 5 messages",
          status: "remote_newer",
          local_updated_at_label: "2026-03-24 10:00 UTC",
          remote_updated_at_label: "2026-03-25 10:00 UTC",
        },
        {
          resource_id: "conv-b",
          selection_id: "conv-b",
          label: "Beta",
          detail: "pear/beta | 1 message",
          status: "only_remote",
          remote_updated_at_label: "2026-03-25 11:00 UTC",
        },
        {
          resource_id: "conv-c",
          selection_id: "conv-c",
          label: "Gamma",
          detail: "notes/gamma | 2 messages",
          status: "only_local",
          local_updated_at_label: "2026-03-25 09:00 UTC",
        },
      ],
      all_items: [
        {
          resource_id: "conv-a",
          selection_id: "conv-a",
          label: "Alpha",
          detail: "pear/alpha | 5 messages",
          status: "remote_newer",
          local_updated_at_label: "2026-03-24 10:00 UTC",
          remote_updated_at_label: "2026-03-25 10:00 UTC",
        },
        {
          resource_id: "conv-b",
          selection_id: "conv-b",
          label: "Beta",
          detail: "pear/beta | 1 message",
          status: "only_remote",
          remote_updated_at_label: "2026-03-25 11:00 UTC",
        },
        {
          resource_id: "conv-c",
          selection_id: "conv-c",
          label: "Gamma",
          detail: "notes/gamma | 2 messages",
          status: "only_local",
          local_updated_at_label: "2026-03-25 09:00 UTC",
        },
      ],
    },
    {
      key: "settings",
      label: "Workspace preferences",
      only_remote: 0,
      remote_newer: 1,
      only_local: 0,
      local_newer: 0,
      identical: 0,
      change_count: 1,
      selected_by_default: true,
      items: [
        {
          resource_id: "settings",
          selection_id: "settings",
          label: "Workspace preferences",
          detail: "",
          status: "remote_newer",
          remote_updated_at_label: "2026-03-25 12:00 UTC",
        },
      ],
      all_items: [
        {
          resource_id: "settings",
          selection_id: "settings",
          label: "Workspace preferences",
          detail: "",
          status: "remote_newer",
          remote_updated_at_label: "2026-03-25 12:00 UTC",
        },
      ],
    },
  ],
  push_sections: [
    {
      key: "conversations",
      label: "Conversations",
      only_remote: 0,
      remote_newer: 0,
      only_local: 1,
      local_newer: 2,
      identical: 4,
      change_count: 3,
      selected_by_default: true,
      items: [],
      all_items: [
        {
          resource_id: "conv-a",
          selection_id: "conv-a",
          label: "Alpha",
          detail: "notes/alpha | 5 messages",
          status: "local_newer",
          local_updated_at_label: "2026-03-25 08:00 UTC",
        },
        {
          resource_id: "conv-d",
          selection_id: "conv-d",
          label: "Delta",
          detail: "notes/delta | 1 message",
          status: "only_local",
          local_updated_at_label: "2026-03-25 08:30 UTC",
        },
        {
          resource_id: "conv-e",
          selection_id: "conv-e",
          label: "Echo",
          detail: "notes/echo | 3 messages",
          status: "local_newer",
          local_updated_at_label: "2026-03-25 09:30 UTC",
        },
      ],
    },
    {
      key: "settings",
      label: "Workspace preferences",
      only_remote: 0,
      remote_newer: 0,
      only_local: 0,
      local_newer: 1,
      identical: 0,
      change_count: 1,
      selected_by_default: true,
      items: [],
      all_items: [
        {
          resource_id: "settings",
          selection_id: "settings",
          label: "Workspace preferences",
          detail: "",
          status: "local_newer",
          local_updated_at_label: "2026-03-25 10:00 UTC",
        },
      ],
    },
  ],
});

describe("KnowledgeSyncTab", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(axios, "get").mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: buildOverview() });
      }
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    vi.spyOn(axios, "post").mockResolvedValue({ data: {} });
  });

  it("shows software and data as equal deployment status dimensions", async () => {
    render(<KnowledgeSyncTab />);

    expect(await screen.findByText("Deployment status")).toBeInTheDocument();
    expect(screen.getByText("0.1.0a1 // b12")).toBeInTheDocument();
    expect(screen.getByText("Studio // d-studio123456")).toBeInTheDocument();
    expect(screen.getByText(/Last synced .* with Pear/i)).toBeInTheDocument();
    expect(document.querySelector('[data-status-dimension="software"]')).toBeInTheDocument();
    expect(document.querySelector('[data-status-dimension="data"]')).toBeInTheDocument();
    expect(document.querySelector(".knowledge-sync-dashboard-grid")).toBeInTheDocument();
  });

  it("keeps local backend network failures distinct from remote peer failures", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.reject(new Error("Network Error"));
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    expect(
      await screen.findByText(
        /Failed to load sync overview\. Check that this Float instance is running and reachable\./i,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/other Float instance/i)).not.toBeInTheDocument();
  });

  it("uses the saved friendly label for actions without claiming a live connection", async () => {
    const overview = buildPairedOverview();
    overview.sync_defaults.saved_peers[0] = {
      ...overview.sync_defaults.saved_peers[0],
      label: "Cherry",
      remote_device_name: "Pear",
    };
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: overview });
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    expect(
      await screen.findByRole("button", { name: /send selected to cherry/i }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("trusted, not checked").length).toBeGreaterThan(0);
    expect(screen.getByText("Trusted Cherry; not checked")).toBeInTheDocument();
    expect(screen.queryByText("Trusted Pear; not checked")).not.toBeInTheDocument();
    expect(screen.queryByText("paired", { exact: true })).not.toBeInTheDocument();
  });

  it("marks a peer identity verified after a successful sync preview", async () => {
    const overview = buildPairedOverview();
    overview.sync_defaults.saved_peers[0] = {
      ...overview.sync_defaults.saved_peers[0],
      label: "Cherry",
      remote_device_name: "Pear",
    };
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: overview });
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/sync/plan") return Promise.resolve({ data: buildPlanResponse() });
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);
    fireEvent.click(await screen.findByRole("button", { name: /preview changes/i }));

    expect(await screen.findByText("Connected to Cherry")).toBeInTheDocument();
    expect(screen.getAllByText("connected", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.queryByText(/identity not verified/i)).not.toBeInTheDocument();
  });

  it("announces pending-review approval beside the review while it applies", async () => {
    const overview = buildOverview();
    overview.sync_reviews.pending = [
      {
        id: "review-1",
        source_label: "Pear",
        device_name: "Pear laptop",
        device_id: "remote-device-1",
        created_at: 1785434400,
        requested_sections: ["settings"],
      },
    ];
    overview.device_counts.pending_push_reviews = 1;
    let overviewCalls = 0;
    let resolveApproval;
    const approvalPromise = new Promise((resolve) => {
      resolveApproval = resolve;
    });
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        overviewCalls += 1;
        return Promise.resolve({ data: overviewCalls === 1 ? overview : buildOverview() });
      }
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/sync/reviews/review-1/approve") return approvalPromise;
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);
    const reviewCard = (await screen.findByText(/device Pear laptop/i)).closest("article");
    const approveButton = within(reviewCard).getByRole("button", { name: "Approve" });
    const rejectButton = within(reviewCard).getByRole("button", { name: "Reject" });
    expect(approveButton).toHaveClass("knowledge-sync-action--primary");
    expect(rejectButton).toHaveClass("knowledge-sync-action--danger");
    fireEvent.click(approveButton);

    expect(await within(reviewCard).findByRole("status")).toHaveTextContent(
      /Applying sync from Pear\. This can take a moment while local indexes refresh\./i,
    );
    expect(within(reviewCard).getByRole("button", { name: "Applying..." })).toBeDisabled();
    expect(within(reviewCard).getByRole("button", { name: "Reject" })).toBeDisabled();

    await act(async () => {
      resolveApproval({
        data: {
          result: {
            sections: { settings: { label: "settings", applied: 1, skipped: 0 } },
          },
        },
      });
      await approvalPromise;
    });

    expect(await screen.findByText(/Approved push from Pear\. settings: 1 applied, 0 skipped/i)).toBeInTheDocument();
  });

  it("toggles the real LAN listener and distinguishes saved from active state", async () => {
    const overview = buildOverview();
    overview.device_access.visibility.lan_listening = false;
    overview.device_access.visibility.lan_state = "restart_required";
    overview.device_access.listener.lan_listening = false;
    overview.device_access.advertised_urls.lan = "";
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: overview });
      if (url === "/api/sync/mobile-serve/status") return Promise.resolve({ data: buildMobileServeStatus() });
      if (url === "/api/user-settings") return Promise.resolve({ data: { device_display_name: "Studio" } });
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/sync/lan-visibility") {
        expect(payload).toEqual({ enabled: false, restart: true });
        return Promise.resolve({
          data: {
            enabled: false,
            restart_scheduled: true,
            message: "Restarting Float's backend in device-only mode.",
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);

    expect(await screen.findByText("restart needed")).toBeInTheDocument();
    expect(screen.getByText("http://studio.local:59185")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate pairing code/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /Visible on LAN/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/sync/lan-visibility", {
        enabled: false,
        restart: true,
      });
    });
    expect(
      await screen.findByText("Restarting Float's backend in device-only mode."),
    ).toBeInTheDocument();
  });

  it("keeps a saved peer's last observed software and data identity visible", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: buildPairedOverview() });
      if (url === "/api/sync/mobile-serve/status") return Promise.resolve({ data: buildMobileServeStatus() });
      if (url === "/api/user-settings") return Promise.resolve({ data: { device_display_name: "Studio" } });
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    expect(
      await screen.findByText(/Last known peer status: software 0\.1\.0a1 \/\/ b11; data Pear \/\/ d-pear12345678\./i),
    ).toBeInTheDocument();
  });

  it("starts Mobile Float Tailscale Serve from the sync panel", async () => {
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/sync/mobile-serve/start") {
        expect(payload).toEqual({ serve_port: 64345 });
        return Promise.resolve({
          data: buildMobileServeStatus({
            running: true,
            status_text: "running",
            warning: "",
            message: "Mobile Float available at http://studio.tail.ts.net:64345/",
          }),
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);

    expect(await screen.findByText("Mobile Float")).toBeInTheDocument();
    const startButton = screen.getByRole("button", { name: /Start serve/i });
    await waitFor(() => expect(startButton).not.toBeDisabled());
    fireEvent.click(startButton);

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith("/api/sync/mobile-serve/start", {
        serve_port: 64345,
      });
    });
    expect(
      await screen.findByText("Mobile Float available at http://studio.tail.ts.net:64345/"),
    ).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("lets you review and uncheck individual sync items before pulling", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: buildPairedOverview() });
      if (url === "/api/sync/mobile-serve/status") return Promise.resolve({ data: buildMobileServeStatus() });
      if (url === "/api/user-settings") return Promise.resolve({ data: { device_display_name: "Studio" } });
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { status: "saved" } });
      }
      if (url === "/api/sync/plan") {
        return Promise.resolve({ data: buildPlanResponse() });
      }
      if (url === "/api/sync/apply") {
        expect(payload).toMatchObject({
          remote_url: "http://peer.float:5000",
          direction: "pull",
          sections: ["conversations", "settings"],
          item_selections: {
            conversations: ["conv-a"],
            settings: ["settings"],
          },
          plan_receipt: "receipt-token",
        });
        return Promise.resolve({
          data: {
            effective_namespace: "Pear",
            result: {
              sections: {
                conversations: { applied: 1, skipped: 0, deleted: 1, delete_skipped: 0 },
                settings: { applied: 1, skipped: 0 },
              },
            },
          },
        });
      }
      return Promise.reject(new Error(`Unexpected POST ${url}`));
    });

    render(<KnowledgeSyncTab />);

    fireEvent.click(await screen.findByRole("button", { name: /preview changes/i }));

    expect(await screen.findByText(/1 add or restore here, 1 update here, 1 delete here, 0 conflicts, 0 known differences, 4 already match\./i)).toBeInTheDocument();
    expect(screen.getByText(/1 add or restore there, 2 update there, 0 delete there, 0 conflicts, 0 known differences, 4 already match\./i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /review pull items \(2\/3\)/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /review pull items \(2\/3\)/i }));

    const dialog = await screen.findByRole("dialog", {
      name: /review pull items for conversations/i,
    });
    expect(
      within(dialog).getByText(/Here 2026-03-24 10:00 UTC \| There 2026-03-25 10:00 UTC/i),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("checkbox", { name: /Beta/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /review pull items \(1\/3\)/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /apply pull here \(2\)/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/sync/apply",
        expect.objectContaining({
          remote_url: "http://peer.float:5000",
          direction: "pull",
          sections: ["conversations", "settings"],
          item_selections: {
            conversations: ["conv-a"],
            settings: ["settings"],
          },
          plan_receipt: "receipt-token",
        }),
        expect.anything(),
      );
    });
    expect(await screen.findByText(/Pull complete\..*Stored under Pear\//i)).toBeInTheDocument();
  });

  it("keeps checkpointed deletions explicit and conflicts non-actionable", async () => {
    const preview = buildPlanResponse();
    const checkpointedItems = [
      {
        resource_id: "conv-deleted-there",
        selection_id: "conv-deleted-there",
        label: "Deleted there",
        status: "remote_deleted",
        baseline_available: true,
      },
      {
        resource_id: "conv-deleted-here",
        selection_id: "conv-deleted-here",
        label: "Deleted here",
        status: "local_deleted",
        baseline_available: true,
      },
      {
        resource_id: "conv-conflict",
        selection_id: "conv-conflict",
        label: "Both edited",
        status: "conflict",
        baseline_available: true,
      },
    ];
    preview.pull_sections[0] = {
      ...preview.pull_sections[0],
      only_remote: 1,
      only_local: 1,
      remote_deleted: 1,
      local_deleted: 1,
      conflicts: 1,
      identical: 2,
      change_count: 3,
      items: checkpointedItems,
      all_items: checkpointedItems,
    };
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: buildPairedOverview() });
      if (url === "/api/sync/mobile-serve/status") return Promise.resolve({ data: buildMobileServeStatus() });
      if (url === "/api/user-settings") return Promise.resolve({ data: { device_display_name: "Studio" } });
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/sync/plan") return Promise.resolve({ data: preview });
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);
    fireEvent.click(await screen.findByRole("button", { name: /preview changes/i }));

    expect(await screen.findByText("Deleted remotely since sync")).toBeInTheDocument();
    expect(screen.getByText("Deleted here since sync")).toBeInTheDocument();
    expect(screen.getByText("Both changed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /review pull items \(1\/2\)/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /review pull items \(1\/2\)/i }));
    const dialog = await screen.findByRole("dialog", {
      name: /review pull items for conversations/i,
    });
    expect(within(dialog).getByText("Delete here")).toBeInTheDocument();
    expect(within(dialog).getByText("Add or restore here")).toBeInTheDocument();
    expect(within(dialog).queryByText("Both edited")).not.toBeInTheDocument();
  });

  it("invalidates a preview when workspace mapping changes", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: buildPairedOverview() });
      if (url === "/api/sync/mobile-serve/status") return Promise.resolve({ data: buildMobileServeStatus() });
      if (url === "/api/user-settings") return Promise.resolve({ data: { device_display_name: "Studio" } });
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/sync/plan") return Promise.resolve({ data: buildPlanResponse() });
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);

    fireEvent.click(await screen.findByRole("button", { name: /preview changes/i }));
    expect(await screen.findByText(/1 add or restore here/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /import nested/i }));

    expect(await screen.findByText(/Sync settings changed\. Preview changes again/i)).toBeInTheDocument();
    expect(screen.queryByText(/1 add or restore here/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pull selected here/i })).toBeDisabled();
    expect(axios.post).not.toHaveBeenCalledWith("/api/sync/apply", expect.anything(), expect.anything());
  });

  it("shows staged sync progress and lets the user cancel preview requests", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: buildPairedOverview() });
      if (url === "/api/sync/mobile-serve/status") return Promise.resolve({ data: buildMobileServeStatus() });
      if (url === "/api/user-settings") return Promise.resolve({ data: { device_display_name: "Studio" } });
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url, payload, config) => {
      if (url !== "/api/sync/plan") {
        return Promise.resolve({ data: {} });
      }
      return new Promise((resolve, reject) => {
        const signal = config?.signal;
        signal?.addEventListener("abort", () => {
          const error = new Error("canceled");
          error.code = "ERR_CANCELED";
          error.name = "CanceledError";
          reject(error);
        });
      });
    });

    render(<KnowledgeSyncTab />);

    fireEvent.click(await screen.findByRole("button", { name: /preview changes/i }));

    expect(await screen.findByText("Previewing sync")).toBeInTheDocument();
    expect(screen.getByText(/Stage-based progress while Float waits on the request\./i)).toBeInTheDocument();
    const stopPreviewButton = screen.getByRole("button", { name: /stop preview/i });
    expect(stopPreviewButton).toHaveClass("knowledge-sync-action--danger");
    fireEvent.click(stopPreviewButton);

    await waitFor(() => {
      expect(
        axios.post.mock.calls.some(([url]) =>
          String(url).startsWith("/api/sync/operations/preview-"),
        ),
      ).toBe(true);
      expect(screen.getByText(/Sync preview cancelled\./i)).toBeInTheDocument();
      expect(screen.getByText(/Preview stopped\./i)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /stop preview/i })).not.toBeInTheDocument();
    });
  });

  it("surfaces the most recent completed sync near the top of the tab", async () => {
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: buildOverview() });
      }
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({
          data: {
            actions: [
              {
                id: "sync-1",
                kind: "sync",
                name: "sync_pull",
                summary: "Sync pull from http://peer.float:5000",
                item_count: 3,
                created_at_ts: 1770000000,
              },
            ],
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    expect(
      await screen.findByText(
        /Last completed sync: Pull completed, 3 changed items at /i,
      ),
    ).toBeInTheDocument();
  });

  it("shows explicit sync ownership and unfinished egress notes", async () => {
    const overview = buildOverview();
    overview.sync_defaults.remote_url = "http://pear.float:5000";
    overview.sync_defaults.saved_peers = [
      {
        id: "peer-pear",
        label: "Pear",
        remote_url: "http://pear.float:5000",
        scopes: ["sync"],
        remote_device_id: "remote-device-1",
      },
    ];
    overview.egress_summary = {
      private_network_only: true,
      inbound_visibility: {
        lan_enabled: true,
        online_requested: false,
        online_supported: false,
      },
      outbound_target: {
        mode: "saved_peer",
        remote_url: "http://pear.float:5000",
        peer_id: "peer-pear",
        peer_label: "Pear",
      },
      push_review_mode: "review_required",
      saved_peer_count: 1,
      unfinished_notice:
        "Automatic stop-kill safeguards are future work. Stop records cancel intent and aborts the current local request where possible, but it does not kill remote work that another device already accepted.",
    };
    overview.sync_operations = {
      active_operation: {
        id: "preview-1",
        kind: "preview",
        status: "running",
        started_at: 1770000000,
        remote_label: "Pear",
        remote_url: "http://pear.float:5000",
        sections: ["conversations"],
        workspace_mode: "merge",
        owner: "Studio",
        cancel_requested: false,
      },
      last_attempt: {
        id: "pull-1",
        kind: "pull",
        status: "completed",
        started_at: 1769999900,
        finished_at: 1770000000,
        remote_label: "Pear",
        remote_url: "http://pear.float:5000",
        sections: ["conversations"],
        workspace_mode: "merge",
        owner: "Studio",
        cancel_requested: false,
      },
      recent: [],
    };
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: overview });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    const syncOwnershipLabel = await screen.findByText("Sync ownership");
    expect(syncOwnershipLabel).toBeInTheDocument();
    expect(syncOwnershipLabel).toHaveAttribute("tabindex", "0");
    expect(syncOwnershipLabel).toHaveAccessibleName(/default outbound target/i);
    expect(screen.getAllByText("Pear").length).toBeGreaterThan(0);
    expect(screen.queryByText("Pear at http://pear.float:5000")).not.toBeInTheDocument();
    expect(screen.getAllByText("Review required").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Unfinished: Automatic stop-kill safeguards are future work\./i),
    ).toBeInTheDocument();
    expect(screen.getByText("Private reachable addresses only")).toBeInTheDocument();
    expect(screen.getByText(/Preview - Running - Pear/i)).toBeInTheDocument();
    expect(screen.getByText(/Pull - Completed - Pear/i)).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /explain sync ownership state/i }));
    const inspector = screen.getByRole("dialog", { name: /why this sync state is shown/i });
    expect(inspector).toBeInTheDocument();
    expect(screen.getByText("/api/sync/overview")).toBeInTheDocument();
    expect(within(inspector).getByText(/Stop records cancel intent/i)).toBeInTheDocument();
  });

  it("shows sync inbox suggestions without starting auto sync", async () => {
    const overview = buildOverview();
    overview.sync_defaults.remote_url = "http://pear.float:5000";
    overview.sync_defaults.saved_peers = [
      {
        id: "peer-pear",
        label: "Pear",
        remote_url: "http://pear.float:5000",
        scopes: ["sync"],
        remote_device_id: "remote-device-1",
        remote_public_key: "pk-pear",
        local_workspace_ids: ["root"],
        remote_workspace_ids: ["root"],
        workspace_mode: "merge",
      },
    ];
    overview.egress_summary.saved_peer_count = 1;
    overview.egress_summary.outbound_target = {
      mode: "saved_peer",
      remote_url: "http://pear.float:5000",
      peer_id: "peer-pear",
      peer_label: "Pear",
    };
    overview.sync_suggestions = [
      {
        id: "ready-reviewed-sync-peer-pear",
        title: "Pear is ready for reviewed sync",
        summary:
          "Auto-sync is still off. This pair has sync scope, a saved fingerprint, and workspace mapping.",
        severity: "info",
        priority: 30,
        action: "check_then_preview",
        action_label: "Check remote, then preview",
        next_step:
          "Check remote to confirm reachability, then Preview sync before pulling or pushing selected sections.",
        peer_id: "peer-pear",
        peer_label: "Pear",
        remote_url: "http://pear.float:5000",
        manual_review_required: true,
        auto_sync_enabled: false,
        auto_sync_available: false,
        requirements: [
          {
            label: "Stable identity",
            status: "ready",
            detail: "Remote fingerprint saved",
          },
          {
            label: "Automatic sync",
            status: "off",
            detail: "Only suggestions are shown; nothing runs automatically",
          },
        ],
        state_explanation: {
          title: "Why Pear is ready for reviewed sync is suggested",
          summary:
            "Auto-sync is still off. This pair has sync scope, a saved fingerprint, and workspace mapping.",
          rows: [
            { label: "Source", value: "/api/sync/overview.sync_suggestions" },
            { label: "Automatic sync", value: "Off" },
            { label: "Review", value: "Manual approval required" },
            {
              label: "Next",
              value:
                "Check remote to confirm reachability, then Preview sync before pulling or pushing selected sections.",
            },
          ],
        },
      },
    ];
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: overview });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    expect(await screen.findByText("Sync inbox")).toBeInTheDocument();
    expect(screen.getByText("Pear is ready for reviewed sync")).toBeInTheDocument();
    expect(screen.getByText("Check remote, then preview")).toBeInTheDocument();
    expect(screen.getByText("Remote fingerprint saved")).toBeInTheDocument();
    expect(screen.getByText("Only suggestions are shown; nothing runs automatically")).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", {
        name: /explain sync suggestion pear is ready for reviewed sync/i,
      }),
    );
    const inspector = screen.getByRole("dialog", {
      name: /why pear is ready for reviewed sync is suggested/i,
    });
    expect(within(inspector).getByText("/api/sync/overview.sync_suggestions")).toBeInTheDocument();
    expect(within(inspector).getByText("Manual approval required")).toBeInTheDocument();
  });

  it("uses backend trust provenance for the legacy cleanup bucket", async () => {
    const overview = buildOverview();
    overview.inbound_devices = [
      {
        id: "trusted-1",
        name: "Pear Laptop",
        status: "trusted_device",
        status_label: "Trusted device",
        created_at: 1770000000,
        last_seen: 1770000060,
        capabilities: { requested_scopes: ["sync"], paired_via_offer: true },
      },
    ];
    overview.legacy_inbound_devices = [
      {
        id: "legacy-ua-1",
        name: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        status: "unverified_legacy_record",
        status_label: "Unverified legacy record",
        legacy_record: true,
        created_at: 1770000000,
        last_seen: 1770000060,
        capabilities: { requested_scopes: ["sync", "stream"] },
      },
    ];
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: overview });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    expect(await screen.findByText("Unverified legacy records")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Prune 1 unverified legacy device record/i }),
    ).toHaveClass("knowledge-sync-action--danger");
    expect(
      screen.getByRole("button", { name: /Revoke trust for Pear Laptop/i }),
    ).toHaveClass("knowledge-sync-action--danger");
    expect(screen.getByText("Pear Laptop")).toBeInTheDocument();
  });

  it("persists workspace privacy settings and private rules", async () => {
    const overview = buildOverview();
    overview.workspaces.profiles.push({
      id: "journal",
      name: "Journal",
      slug: "journal",
      namespace: "journal",
      root_path: "data/files/workspace/journal",
      kind: "local",
      privacy_mode: "default",
      private_patterns: [],
    });
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: overview });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/user-settings") {
        expect(payload).toMatchObject({
          active_workspace_id: "root",
          workspace_profiles: expect.arrayContaining([
            expect.objectContaining({
              id: "root",
              privacy_mode: "secret",
              private_patterns: ["notes/private/*", "*.pem"],
            }),
            expect.objectContaining({
              id: "journal",
              privacy_mode: "default",
            }),
          ]),
        });
        return Promise.resolve({ data: { status: "saved" } });
      }
      return Promise.reject(new Error(`Unexpected POST ${url}`));
    });

    render(<KnowledgeSyncTab />);

    fireEvent.change(await screen.findByLabelText(/Workspace privacy for Main workspace/i), {
      target: { value: "secret" },
    });
    fireEvent.change(screen.getByLabelText(/Private match rules for Main workspace/i), {
      target: { value: "notes/private/*\n*.pem" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save device settings/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/user-settings",
        expect.objectContaining({
          workspace_profiles: expect.arrayContaining([
            expect.objectContaining({
              id: "root",
              privacy_mode: "secret",
              private_patterns: ["notes/private/*", "*.pem"],
            }),
          ]),
        }),
      );
    });
    expect(await screen.findByText(/Device and sync defaults saved\./i)).toBeInTheDocument();
  });

  it("shows inherited workspace lineage and the upstream sync-back target", async () => {
    const overview = buildOverview();
    overview.workspaces.profiles.push({
      id: "sync-pear-self",
      name: "Pear / Self",
      slug: "pear-self",
      namespace: "Pear/self",
      root_path: "data/sync/Pear/self",
      kind: "synced",
      imported: true,
      source_peer_id: "peer-pear",
      source_device_name: "Pear",
      source_workspace_id: "self",
      source_workspace_name: "Self",
      lineage_id: "self-lineage-uuid",
      origin_deployment_id: "origin-deployment-uuid",
      upstream_deployment_id: "upstream-deployment-uuid",
    });
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: overview });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });

    render(<KnowledgeSyncTab />);

    const source = await screen.findByText(/Source: Pear \/ Self/i);
    const workspaceCard = source.closest("article");
    expect(workspaceCard).toHaveTextContent(/sync back to upstream-dep/i);
    expect(workspaceCard).toHaveTextContent(/Lineage self-lineage/i);
    expect(workspaceCard).toHaveTextContent(/origin origin-deplo/i);
  });

  it("requires address verification before saving or previewing a moved pair", async () => {
    const overview = buildOverview();
    overview.sync_defaults.saved_peers = [
      {
        id: "peer-pear",
        label: "Pear",
        remote_url: "http://pear.local:59185",
        scopes: ["sync"],
        remote_device_id: "remote-device-1",
        remote_public_key: "pk-pear",
        remote_device_name: "Pear",
      },
    ];
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") {
        return Promise.resolve({ data: overview });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") {
        return Promise.resolve({ data: { actions: [] } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url) => {
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { status: "saved" } });
      }
      if (url === "/api/sync/plan") {
        return Promise.reject(new Error("Preview should wait for fingerprint check"));
      }
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);

    fireEvent.click(await screen.findByRole("button", { name: /pear/i }));
    fireEvent.change(screen.getByLabelText("Remote Float URL"), {
      target: { value: "http://pear.local:61234" },
    });
    expect(screen.getByRole("button", { name: /preview changes/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /save connection settings/i })).toBeDisabled();
    expect(
      screen.getAllByRole("button", { name: /check and save new address/i }).length,
    ).toBeGreaterThan(0);
    expect(
      await screen.findByText(/A failed check leaves the saved address unchanged/i),
    ).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/sync/plan",
      expect.anything(),
      expect.anything(),
    );
  });

  it("keeps the saved address and explains a failed moved-address check", async () => {
    const overview = buildPairedOverview("http://pear.local:59185");
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: overview });
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/sync/peer/status") {
        expect(payload.update_saved_peer).toBe(true);
        expect(payload.remote_url).toBe("http://pear.local:61234");
        const error = new Error("network request failed");
        error.response = {
          data: {
            detail:
              "Remote status check failed: HTTPConnectionPool(host='pear.local'): NameResolutionError getaddrinfo failed",
          },
        };
        return Promise.reject(error);
      }
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);
    fireEvent.change(await screen.findByLabelText("Remote Float URL"), {
      target: { value: "http://pear.local:61234" },
    });
    fireEvent.click(
      screen.getAllByRole("button", { name: /check and save new address/i })[0],
    );

    expect(
      await screen.findByText(
        /Saved address remains http:\/\/pear\.local:59185\. Remote device is not reachable right now\. Check the remote address/i,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/HTTPConnectionPool/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Remote Float URL")).toHaveValue("http://pear.local:61234");
    expect(screen.getByText("new address unverified")).toBeInTheDocument();
  });

  it("checks and saves a moved address when the stored fingerprint matches", async () => {
    const overview = buildPairedOverview("http://pear.local:59185");
    const savedPeer = overview.sync_defaults.saved_peers[0];
    axios.get.mockImplementation((url) => {
      if (url === "/api/sync/overview") return Promise.resolve({ data: overview });
      if (url === "/api/sync/mobile-serve/status") {
        return Promise.resolve({ data: buildMobileServeStatus() });
      }
      if (url === "/api/user-settings") {
        return Promise.resolve({ data: { device_display_name: "Studio" } });
      }
      if (url === "/api/actions") return Promise.resolve({ data: { actions: [] } });
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    axios.post.mockImplementation((url, payload) => {
      if (url === "/api/sync/peer/status") {
        expect(payload).toEqual(
          expect.objectContaining({
            remote_url: "http://pear.local:61234",
            update_saved_peer: true,
          }),
        );
        return Promise.resolve({
          data: {
            reachable: true,
            identity_verified: true,
            identity_state: "verified",
            display_name: "Pear",
            paired_device: { ...savedPeer, remote_url: "http://pear.local:61234" },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    render(<KnowledgeSyncTab />);
    fireEvent.change(await screen.findByLabelText("Remote Float URL"), {
      target: { value: "http://pear.local:61234" },
    });
    fireEvent.click(
      screen.getAllByRole("button", { name: /check and save new address/i })[0],
    );

    expect(await screen.findByText("Verified Pear; saved URL updated.")).toBeInTheDocument();
    expect(screen.getAllByText("connected", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText("Last known address: http://pear.local:61234")).toBeInTheDocument();
  });

  it("routes ordinary Markdown to Documents only after explicit confirmation", async () => {
    axios.post.mockImplementation((url) => {
      if (url === "/api/conversations/import/preview") {
        return Promise.resolve({
          data: {
            detected_files: [
              {
                path: "profile.md",
                classification: "document",
                message_count: 0,
                suggested_action: "document",
                allowed_actions: ["document"],
                preview: "# Profile",
                warnings: [],
              },
            ],
          },
        });
      }
      if (url === "/api/knowledge/upload") {
        return Promise.resolve({ data: { id: "profile-doc" } });
      }
      return Promise.resolve({ data: {} });
    });
    const { container } = render(<KnowledgeSyncTab />);
    await screen.findByText("Import and export");
    const input = container.querySelector('input.knowledge-sync-hidden-input[type="file"]');
    const file = new File(["# Profile\n\nLikes pears."], "profile.md", {
      type: "text/markdown",
    });

    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("Document detected")).toBeInTheDocument();
    expect(axios.post).toHaveBeenCalledWith(
      "/api/conversations/import/preview",
      expect.any(FormData),
    );
    expect(
      axios.post.mock.calls.filter(([url]) =>
        ["/api/conversations/import", "/api/knowledge/upload"].includes(url),
      ),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /save as document/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/knowledge/upload",
        expect.any(FormData),
      );
    });
    const documentCall = axios.post.mock.calls.find(
      ([url]) => url === "/api/knowledge/upload",
    );
    expect(documentCall[1].get("file")).toBe(file);
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/conversations/import",
      expect.anything(),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Saved profile.md to Documents and knowledge search.",
    );
  });

  it("announces a document import failure as an alert", async () => {
    axios.post.mockImplementation((url) => {
      if (url === "/api/conversations/import/preview") {
        return Promise.resolve({
          data: {
            classification: "document",
            message_count: 0,
            suggested_action: "document",
            allowed_actions: ["document"],
            warnings: [],
          },
        });
      }
      if (url === "/api/knowledge/upload") {
        return Promise.reject({
          response: { data: { detail: "Document storage unavailable" } },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const { container } = render(<KnowledgeSyncTab />);
    await screen.findByText("Import and export");
    const input = container.querySelector('input.knowledge-sync-hidden-input[type="file"]');

    fireEvent.change(input, {
      target: {
        files: [new File(["# Notes"], "notes.md", { type: "text/markdown" })],
      },
    });

    expect(await screen.findByText("Document detected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /save as document/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Document storage unavailable",
    );
  });

  it("imports a recognized transcript only after the conversation action is clicked", async () => {
    axios.post.mockImplementation((url) => {
      if (url === "/api/conversations/import/preview") {
        return Promise.resolve({
          data: {
            classification: "conversation",
            message_count: 2,
            role_counts: { user: 1, ai: 1 },
            suggested_action: "conversation",
            allowed_actions: ["conversation", "document"],
          },
        });
      }
      if (url === "/api/conversations/import") {
        return Promise.resolve({
          data: { status: "imported", name: "sync-transcript", message_count: 2 },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const { container } = render(<KnowledgeSyncTab />);
    await screen.findByText("Import and export");
    const input = container.querySelector('input.knowledge-sync-hidden-input[type="file"]');
    const file = new File(["### [user]\nHello\n### [ai]\nHi"], "float-chat.markdown", {
      type: "text/markdown",
    });

    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("Conversation transcript detected")).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/conversations/import",
      expect.anything(),
    );
    fireEvent.click(screen.getByRole("button", { name: /^import conversation$/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/conversations/import",
        expect.any(FormData),
      );
    });
    const conversationCall = axios.post.mock.calls.find(
      ([url]) => url === "/api/conversations/import",
    );
    expect(conversationCall[1].get("intent")).toBe("conversation");
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/knowledge/upload",
      expect.anything(),
    );
  });

  it("does not write ambiguous Markdown until its recognized messages are explicitly chosen", async () => {
    axios.post.mockImplementation((url) => {
      if (url === "/api/conversations/import/preview") {
        return Promise.resolve({
          data: {
            classification: "ambiguous",
            message_count: 1,
            role_counts: { user: 1 },
            suggested_action: "review",
            allowed_actions: ["conversation", "document"],
            warnings: ["Unstructured content appears outside the recognized transcript."],
          },
        });
      }
      if (url === "/api/conversations/import") {
        return Promise.resolve({
          data: { status: "imported", name: "reviewed-mixed", message_count: 1 },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const { container } = render(<KnowledgeSyncTab />);
    await screen.findByText("Import and export");
    const input = container.querySelector('input.knowledge-sync-hidden-input[type="file"]');
    const file = new File(["# Notes\n### [user]\nHello"], "mixed.md", {
      type: "text/markdown",
    });

    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("Mixed or ambiguous Markdown")).toBeInTheDocument();
    expect(screen.getByText(/outside the recognized transcript/i)).toBeInTheDocument();
    expect(axios.post).toHaveBeenCalledWith(
      "/api/conversations/import/preview",
      expect.any(FormData),
    );
    expect(
      axios.post.mock.calls.filter(([url]) =>
        ["/api/conversations/import", "/api/knowledge/upload"].includes(url),
      ),
    ).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /import recognized messages/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/conversations/import",
        expect.any(FormData),
      );
    });
    const conversationCall = axios.post.mock.calls.find(
      ([url]) => url === "/api/conversations/import",
    );
    expect(conversationCall[1].get("intent")).toBe("conversation");
    expect(conversationCall[1].get("confirm_ambiguous")).toBe("true");
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/knowledge/upload",
      expect.anything(),
    );
  });

  it("preserves reviewed JSON selection as a conversation-only import", async () => {
    axios.post.mockImplementation((url) => {
      if (url === "/api/conversations/import/preview") {
        return Promise.resolve({
          data: {
            detected_files: [{ path: "exports/sync-chat.json", message_count: 4 }],
          },
        });
      }
      if (url === "/api/conversations/import") {
        return Promise.resolve({
          data: {
            status: "imported",
            imports: [{ name: "imports/sync-chat" }],
            message_count: 4,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    const { container } = render(<KnowledgeSyncTab />);
    await screen.findByText("Import and export");
    const input = container.querySelector('input.knowledge-sync-hidden-input[type="file"]');
    fireEvent.change(input, {
      target: {
        files: [new File(["{}"], "conversations.json", { type: "application/json" })],
      },
    });

    expect(await screen.findByText("exports/sync-chat.json")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /import selected/i }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        "/api/conversations/import",
        expect.any(FormData),
      );
    });
    const conversationCall = axios.post.mock.calls.find(
      ([url]) => url === "/api/conversations/import",
    );
    expect(JSON.parse(conversationCall[1].get("selected_files"))).toEqual([
      "exports/sync-chat.json",
    ]);
    expect(conversationCall[1].get("format")).toBe("json");
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/knowledge/upload",
      expect.anything(),
    );
  });
});
