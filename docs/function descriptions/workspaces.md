# Workspaces

## Purpose

Workspaces give Float named, user-managed roots for files and synced state.

They are meant to cover cases like:
- `work` and `personal` on one machine,
- a desktop importing a laptop workspace as a nested copy,
- later merging or reorganizing those copies without losing source custody.
- float agents with partitioned knowledge and skills for different projects

See also: `knowledge sync.md`.

## Current Model

- Every instance always has one root workspace:
  - `Main workspace`
  - unnamespaced
  - default root path `data/files/workspace`
- Additional local workspaces can be created with:
  - a stable workspace id,
  - a display name,
  - a namespace,
  - a root path.
- Imported sync copies become `synced` workspaces automatically after an import-mode pull.

Each workspace profile tracks enough source metadata to explain where it came from:
- `source_peer_id`
- `source_device_name`
- `source_workspace_id`
- `source_workspace_name`

## What The UI Does Now

- Shows all workspace profiles in `Knowledge > Sync`.
- Lets the user:
  - mark the active workspace,
  - choose which local workspaces are included in sync,
  - add/remove local workspace profiles,
  - choose pull and push target workspaces,
  - choose `merge` or `import nested` mode.

The root workspace stays special:
- it is always present,
- it stays unnamespaced,
- it acts as the default merge target.

## Sync Behavior

- `merge`
  - selected remote data is merged into the chosen target workspace namespace.
  - use this when two devices should contribute to the same logical workspace.
- `import nested`
  - remote data is pulled into a nested source-owned namespace under the chosen target workspace.
  - float then records a `synced` workspace profile for that imported copy.
  - use this when you want a separate imported workspace instead of flattening everything together.

Current sync selection rules:
- pull/push can include multiple workspaces in `merge` mode.
- `import nested` currently supports one source workspace per side.

## Recursion Guard

Imported nested workspaces record which peer they came from.

When a device later syncs back to that same peer, float ignores local workspaces that originated from that peer so it does not create a recursive sync tree.

That means:
- desktop can import laptop workspace inside desktop,
- laptop can sync to desktop again,
- laptop will not try to send the desktop-hosted copy of the laptop workspace back to itself.

## Current Limits

- Workspace switching is currently strongest inside the Sync surface; broader app-wide workspace switching is still early.
- Remote workspace labels are only as good as the other device's saved workspace metadata.
- Import-mode conflict review is still basic.
- There is not yet a dedicated merge/rehome tool for combining two existing workspaces after import.
- Broader custody/history UI outside the sync page still needs work.

## Planned Follow-Ups

- broader app-level workspace switching and filtering,
- clearer workspace custody/history views,
- richer merge/rehome operations after import,
- sidebar/console notifications for push approval and sync results,
- deeper document readers for synced content such as CSV and notebook/Jupyter files.
