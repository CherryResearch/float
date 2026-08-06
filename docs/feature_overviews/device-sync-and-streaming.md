# Device sync and streaming

Device sync is the alpha layer that lets Float move selected state between trusted personal devices. Conversations, memories, knowledge, graph rows, attachments, Calendar items, and workspace preferences can be previewed and reviewed before a pull or send is applied.

Pairing records identity and trust separately from the current connection address. A saved peer can be checked at a new private LAN or Tailnet address only when its fingerprint still matches, and Float does not silently persist an unverified alternate address. Preview receipts are scoped to the selected peer, workspaces, sections, and item choices; drift or a stale receipt requires a fresh preview before apply.

Incoming pushes remain reviewable unless the receiver explicitly enables auto-accept. Cancellation records intent but does not claim rollback for remote work that already completed. Calendar deletion through Sync fails closed while the event or its durable Activity ledger still reports active work, and terminal retained run history is projected before the source event is removed.

The broader multi-device story is still unfinished. Sync is reviewed and user-triggered rather than continuous background replication, address continuity uses stored fingerprint equality rather than a signed challenge-response, public internet exposure is out of scope, and live-session streaming remains separate from the data-sync contract.

The public references for the current shipped surface are `README.md`, `docs/data_directory.md`, and `docs/api_reference.md`.
