# Knowledge Sync

## Purpose

Knowledge Sync lets one trusted Float device compare against another and move state between them.

It is designed for personal, self-managed use first:
- pair named devices explicitly,
- use private transport such as LAN, VPN, or a private tailnet,
- preview changes before applying them,
- keep import/export in the same surface.

See also: `workspaces.md`.

## What It Does Now

- Shows the current device name, visibility state, and advertised LAN URL.
- Generates one-time pairing codes.
- Stores paired devices with names, scopes, remote ids, and remembered workspace mapping.
- Probes a remote URL and reports whether that device is reachable right now.
- Shows inbound trusted-device records on the host device.
- Supports `merge` and `import nested` workspace sync modes.
- Previews pull and push changes by section before applying them.
- Supports review-first or auto-accept behavior for incoming pushes.
- Keeps source-custody metadata on synced content and groups synced files under source-named folders in Documents.
- Includes local import/export in the same Knowledge sync surface.

Current sync sections:
- conversations
- memories
- knowledge
- graph
- attachments
- calendar
- workspace preferences

## Security Model

- Trust is device-based, not account-based.
- Pairing uses a one-time code.
- Public internet device access is blocked.
- LAN/private-network access can be turned on or off live.
- Sync and streaming are intended to sit on private transport, not public exposure.

## Connection States

The current UI exposes two directions of state:

- Outbound:
  - saved target
  - paired
  - connected
- Inbound on the host:
  - trusted device
  - paired device
  - connected device
  - legacy browser record

`connected` should mean the other device is reachable now, not just remembered.

## Workspaces And Custody

Sync is now workspace-aware.

- Local and remote source workspaces can be selected.
- Pull and push each choose a target workspace.
- `merge` keeps data in the destination workspace namespace.
- `import nested` creates a source-owned nested copy and records it as a synced workspace profile.

Imported workspaces carry source metadata so syncing back to the same origin can ignore that nested copy and avoid recursive trees.

## Basic Flow

1. On the device that will receive connections, turn on `Visible on LAN`.
2. Copy the advertised `LAN URL`.
3. Generate a one-time pairing code on that device.
4. On the other device, enter the LAN URL and pairing code.
5. Save or update the paired device entry.
6. Choose workspace mapping and sync mode.
7. Use `Preview sync`.
8. Apply a pull or push only for the sections you want.

## Current Limits

- No public gateway or relay is active yet.
- `import nested` currently supports one source workspace per side.
- Broader workspace switching outside the sync surface is still early.
- Review history and diffs are functional but still raw.
- Broader notifications outside the sync page still need work.
