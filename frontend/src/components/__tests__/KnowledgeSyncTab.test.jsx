import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "axios";

import KnowledgeSyncTab from "../KnowledgeSyncTab";

expect.extend(matchers);

const buildOverview = () => ({
  current_device: {
    display_name: "Studio",
    hostname: "studio-host",
    source_namespace: "Studio",
  },
  device_access: {
    visibility: {
      lan_enabled: true,
    },
    advertised_urls: {
      lan: "http://studio.local:59185",
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
    unfinished_notice:
      "Automatic stop-kill safeguards are future work. Stop records cancel intent and aborts the current local request where possible, but it does not kill remote work that another device already accepted.",
  },
  sync_operations: {
    active_operation: null,
    last_attempt: null,
    recent: [],
  },
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

const buildPlanResponse = () => ({
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

  it("lets you review and uncheck individual sync items before pulling", async () => {
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
        });
        return Promise.resolve({
          data: {
            effective_namespace: "Pear",
            result: {
              sections: {
                conversations: { applied: 1, skipped: 0 },
                settings: { applied: 1, skipped: 0 },
              },
            },
          },
        });
      }
      return Promise.reject(new Error(`Unexpected POST ${url}`));
    });

    render(<KnowledgeSyncTab />);

    fireEvent.change(await screen.findByLabelText("Remote Float URL"), {
      target: { value: "http://peer.float:5000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /preview sync/i }));

    expect(await screen.findByText(/1 new on Pear, 1 newer on Pear, 4 already match here\./i)).toBeInTheDocument();
    expect(screen.getByText(/1 only on this device, 2 newer here, 4 already match there\./i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /review pull items \(2\/2\)/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /review pull items \(2\/2\)/i }));

    const dialog = await screen.findByRole("dialog", {
      name: /review pull items for conversations/i,
    });
    expect(
      within(dialog).getByText(/Here 2026-03-24 10:00 UTC \| There 2026-03-25 10:00 UTC/i),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("checkbox", { name: /Beta/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /review pull items \(1\/2\)/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /pull here/i }));

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
        }),
        expect.anything(),
      );
    });
    expect(await screen.findByText(/Pull complete\..*Stored under Pear\//i)).toBeInTheDocument();
  });

  it("shows staged sync progress and lets the user cancel preview requests", async () => {
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

    fireEvent.change(await screen.findByLabelText("Remote Float URL"), {
      target: { value: "http://192.168.50.45:59185" },
    });
    fireEvent.click(screen.getByRole("button", { name: /preview sync/i }));

    expect(await screen.findByText("Previewing sync")).toBeInTheDocument();
    expect(screen.getByText(/Stage-based progress while Float waits on the request\./i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop preview/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop preview/i }));

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
        /Last completed sync: Sync pull from http:\/\/peer\.float:5000, 3 changed items at /i,
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

    expect(await screen.findByText("Sync ownership")).toBeInTheDocument();
    expect(screen.getByText("Pear at http://pear.float:5000")).toBeInTheDocument();
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

  it("moves browser-shaped trusted-device records into the legacy cleanup bucket", async () => {
    const overview = buildOverview();
    overview.inbound_devices = [
      {
        id: "trusted-1",
        name: "Pear Laptop",
        status: "trusted_device",
        status_label: "Trusted device",
        created_at: 1770000000,
        last_seen: 1770000060,
        capabilities: { requested_scopes: ["sync"] },
      },
      {
        id: "legacy-ua-1",
        name: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        status: "trusted_device",
        status_label: "Trusted device",
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

    expect(await screen.findByText("Legacy browser records")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Prune 1/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(1);
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

  it("requires a remote check before previewing a saved pair at a changed address", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /preview sync/i }));

    expect(
      await screen.findByText(/Check remote before previewing from a changed address/i),
    ).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalledWith(
      "/api/sync/plan",
      expect.anything(),
      expect.anything(),
    );
  });
});
