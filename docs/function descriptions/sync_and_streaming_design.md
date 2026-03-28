## Multi‑Device Sync and Streaming: High‑Level Design and Objectives

### Purpose and scope
This document defines the goals, architecture, and protocols for syncing a user’s calendars and knowledge across devices, and for realtime streaming (A/V and data) between those devices. It focuses on privacy, reliability, and cross‑platform compatibility while aligning with Float’s existing components (e.g., `backend/streaming/livekit_service.py`, calendar import services, and frontend WebRTC capabilities).

### Objectives
- **Reliability first**: Eventual consistency for sync; graceful degradation for streaming (P2P → TURN/SFU fallback).
- **Low‑latency UX**: Prioritize direct paths (LAN/P2P) and adaptive congestion control (WebRTC/QUIC).
- **Privacy by default**: End‑to‑end encryption where feasible; metadata minimization; least‑privilege tokens.
- **Offline‑capable**: Queue operations locally; delta/cursor‑based reconciliation when back online.
- **Provider interoperability**: Native deltas for Google/Microsoft; CalDAV compatible; normalized internal model.
- **Incremental rollout**: Server‑mediated design with optional P2P fast paths; feature‑flagged components.

### Primary use cases
- **Calendar sync**: Two‑way updates across Google/Microsoft and local personal calendars; correct handling of recurring events and instance overrides.
- **Knowledge sync**: Notes, documents, and embeddings synchronized across devices with deduplication and resumable transfers.
- **Realtime device streaming**:
  - A/V sessions (voice/video) between a user’s devices.
  - Data channels for control messages, file chunks, and telemetry.

## Architecture overview

### Logical layers
1. **Identity & device registry**
   - Per‑user and per‑device identities.
   - Device enrollment and key management; presence and capabilities.

2. **Transport abstraction**
   - WebRTC (preferred for browser + native), QUIC/WebTransport for server relay, WebSockets/TCP as baseline.
   - Discovery on LAN via mDNS/DNS‑SD; signaling via central server when required.

3. **Sync engine**
   - Delta/cursor APIs; conflict resolution; batching and backoff.
   - Object store for blobs; metadata store for versions and references.

4. **Streaming engine**
   - Session setup, codec/bitrate negotiation, and adaptive control.
   - Fallback to TURN or SFU (`LiveKit`) when direct paths fail or for multiparty.

5. **Notifications**
   - Push invalidations via Web Push/APNs/FCM; WebSocket presence for active sessions.

6. **Storage & indexing**
   - S3‑compatible blob storage; Postgres for metadata; vector index (pgvector/Qdrant) keyed by content‑hash.

### Key components in this repo
- Backend services under `backend/app/services/` (e.g., calendar import, RAG).
- Streaming under `backend/streaming/` (LiveKit integration available as SFU relay).
- Frontend `src/` for WebRTC client, push, and device UI.

## Identity, auth, and security
- **Device identity**: Each device holds an Ed25519 keypair. Public keys registered with the server and bound to the user.
- **Session auth**: Short‑lived JWTs include user, device ID, scopes, and are proof‑of‑possession bound to the device public key.
- **Transport security**: TLS 1.3/mTLS for HTTPS; DTLS/SRTP for WebRTC; QUIC for WebTransport with per‑stream flow control.
- **E2EE for knowledge** (optional): Per‑object data encryption key (DEK) wrapped by a per‑user KEK; server stores only ciphertext and wrapped keys.
- **Token hygiene**: Provider OAuth tokens stored securely; refresh/rotate; least scopes needed for calendar access.

## Transports and connectivity

### Discovery and signaling
- **LAN discovery**: mDNS/DNS‑SD (Bonjour) to advertise and discover devices on the same network.
- **Central signaling**: A server endpoint brokers session setup (ICE candidates, SDP), presence, and policy enforcement.

### NAT traversal and relay
- **STUN**: Discover reflexive addresses for P2P WebRTC.
- **TURN**: Relay media/data when P2P is blocked.
- **SFU**: Use `LiveKit` for multiparty A/V or policy‑forced relay.

### Additional proximity transports
- **Bluetooth/BLE**: Bootstrap pairing and small control payloads; escalate to Wi‑Fi Direct or LAN for bulk streaming.
- **Wi‑Fi Direct / Wi‑Fi Aware**: High‑throughput local links for large transfers when available.

## Sync design

### Calendars
- **Providers**:
  - Google Calendar: incremental `syncToken` + watch channels.
  - Microsoft 365: Graph `/events/delta` + subscriptions.
  - CalDAV: ETag/CTag + REPORT‑based sync.
- **Normalization**: Internal model captures `UID`, `SEQUENCE`, `LAST-MODIFIED`, `RECURRENCE-ID`, `RRULE`, `EXDATE`, and IANA time zones.
- **Instance addressing**: Treat each overridden instance as a distinct key `(UID, RECURRENCE-ID)` for conflict and storage.
- **Conflict resolution**:
  - Higher `SEQUENCE` wins; if equal, later `LAST-MODIFIED`.
  - Merge conservative fields (e.g., attendees) where safe; surface hard conflicts to the user.
- **Two‑way sync**:
  - Outbound writes use ETag/If‑Match and provider sequence semantics.
  - Server maintains provider cursors and exposes a unified delta feed to devices via `syncCursor`.

### Knowledge
- **Object model**: Content‑addressed blobs using `content_hash` (SHA‑256), plus a small metadata record `{mime, size, metadata_version, links, tags}`.
- **Delta**: Devices request changes since a `syncCursor`; server returns changed metadata and required blob IDs/chunk IDs.
- **Chunking**: Large blobs are chunked (e.g., 4–8 MB) with rolling hash; resume uploads/downloads per chunk.
- **Embeddings**: Indexed by `content_hash`; recompute only when content changes; maintain mapping logical_doc → latest hash.
- **Collaboration formats**: CRDTs (Yjs/Automerge) for notes/structured docs enable offline edits and conflict‑free merges.
- **Conflicts**:
  - Text/notes: CRDT convergence.
  - Binary: last‑writer‑wins with retained previous version; attempt 3‑way merges for known text types when possible.

## Streaming design
- **Default path**: WebRTC P2P with data channels for control and file signaling; SRTP for media.
- **Fallbacks**: TURN for strict NATs; `LiveKit` SFU for multiparty or policy‑enforced relay.
- **QoS**: Separate streams for control vs bulk; prioritize control; leverage WebRTC congestion control.
- **Resilience**: Mid‑stream ICE restarts; QUIC/WebTransport resumption for server‑relayed data streams.

## Server APIs (sketch)

### Device and identity
- `POST /devices/register` → issue device ID, bind public key.
- `POST /devices/token` → short‑lived JWT scoped to sync/stream.

### Sync
- `GET /sync/cursor` → latest cursor and capabilities.
- `POST /sync/changes` with `{cursor}` → returns `{changes, next_cursor}`.
- `POST /sync/upload` and `GET /sync/download` for blobs/chunks.

### Streaming/signaling
- `POST /stream/sessions` → create session; returns SDP/ICE info.
- `POST /stream/candidates` → trickle ICE; `DELETE /stream/sessions/{id}` to end.

### Notifications
- `POST /push/subscribe` → register push endpoint; server emits invalidations `{type, object_id, new_version}`.

## Notifications and background work
- **Push**: Deliver invalidations to prompt devices to pull deltas.
- **WebSocket presence**: For active devices to coordinate fast‑path streams and live cursor updates.
- **Workers**: Background jobs to poll provider deltas, process webhooks, compute embeddings, and compact old versions.

## Privacy and security posture
- **Data minimization**: Store only what’s needed; prefer P2P paths so content does not traverse servers when possible.
- **E2EE knowledge**: Optional per‑workspace/user policy; keys managed client‑side; server unaware of cleartext.
- **Auditability**: Per‑operation logs attributed to user and device; redaction of sensitive payloads.

## Failure modes and recovery
- **Sync**: Idempotent apply via operation IDs; automatic backoff and retry; cursor invalidation triggers full delta recovery.
- **Streaming**: ICE restart, transport switch (P2P → TURN/SFU), and session renegotiation.
- **Local storage**: Corruption detection via hashes; re‑fetch on mismatch.

## Rollout plan (phased)
1. **Phase 1 – Centralized baseline**
   - Server‑mediated sync with delta cursors; WebRTC with central signaling + TURN fallback; LiveKit for multiparty A/V.
2. **Phase 2 – Local‑first enhancements**
   - mDNS LAN discovery and direct P2P fast‑path for bulk transfers; resumable chunking; optional E2EE for knowledge.
3. **Phase 3 – Advanced collaboration**
   - CRDT‑backed notes; presence and cursors; conflict UI for calendar event merges.

## Metrics and observability
- **Sync**: Cursor lag, change set sizes, retry counts, conflict rates.
- **Streaming**: Establishment success rate, RTT, jitter, bitrate, relay ratio (P2P vs TURN/SFU).
- **Storage**: Dedup ratio, chunk reuse, embedding recalc volume.

## Open questions
- Policy for when to force relay (compliance, enterprise network constraints)?
- Default E2EE posture for knowledge vs optional?
- Device limit and eviction strategy per user?
- How to surface complex calendar conflicts without overwhelming users?

---

This design balances a pragmatic server‑mediated foundation with optional P2P optimizations. It leverages provider‑native calendar deltas, content‑addressed knowledge storage, and standard transports (WebRTC/QUIC) to provide a private, reliable, and low‑latency multi‑device experience.


