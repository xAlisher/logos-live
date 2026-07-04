# ADR-0001 — Zone-scanner v0.2: fix the Rust walker, don't retire it (2026-07-04)

**Status:** Accepted

## Context

The v0.2 chain fork broke the Rust `zone-scanner` (walks `/cryptarchia/blocks` for
opcode-17 inscriptions). The probe (#17, `docs/plans/v0.2-api-diff.md`) surfaced a fork:

- **(A)** Fix the Rust scanner — repoint block fetch to `headers → GET /cryptarchia/blocks/{hash}`,
  accept hex-or-int-array inscriptions, adopt the v0.2 raw-hex channel→name scheme, reset the
  dead-locked state.
- **(B)** Retire the Rust scanner — consume the runbook's `zone-board-v0.2.2/zone_scan.json`
  and/or scrape node logs for `ChannelInscribe`, as the already-working :8090 dashboard
  (`~/logos-blockchain-runbook/dashboard/server.py`) does.

## Decision

**Option A.** Keep and fix the Rust `zone-scanner` so logos.live's map pipeline stays
**self-contained** and does not depend on the runbook's zone-board client or its 25 MB state
file. The block-explorer-template (`~/basecamp/refs/logos-blockchain-block-explorer-template`)
remains the *decode reference* for the v0.2 `ChannelInscribe` op, not a deployed dependency.

Decided by Alisher, 2026-07-04.

## Consequences

- **+** logos.live owns its zone-board ingestion end-to-end (one repo, one deploy); no coupling
  to the runbook's tmux zone-board client.
- **+** The Rust scanner's dedup/state model and `zone_scan.json` schema are preserved —
  `publish.py` / `server.py` / frontend consumers need no format change.
- **−** We re-implement v0.2 `ChannelInscribe` decode in Rust (hex-or-int-array inscription,
  raw-hex channel id, new `parent`/`signer` fields) rather than reusing the Python explorer.
- **−** Must keep the Rust decode in lockstep with the Python mirror in `publish.py`.
- Scope lands in **#19** (parser) + **#24** (state reset). The :8090 dashboard's log-scrape
  approach is noted as a fallback if the `/cryptarchia/blocks/{hash}` walk proves too slow at
  scale.
