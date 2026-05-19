# Retro Log

Post-merge retrospectives per `~/fieldcraft/protocols/wins-and-fails.md`.

---

## Retrofit — Fieldcraft Scaffold (2026-05-17)

### Process wins
- Fresh retrofit (no legacy docs to migrate) is fast — all artifacts created in one session
- PROJECT_KNOWLEDGE.md dashboard pattern works well: 3-section split (architecture, lessons, ecosystem) covers all knowledge types

### Process fails
- None (first session)

### Project lessons added
- See `docs/skills/lessons.md` (initial 12 lessons extracted from README.md + MAINTENANCE.md)

### Feedback for Alisher
- `debug/node_modules/` looks unused — worth a cleanup pass when convenient

---

## 2026-05-17 — Community support + Devisha pipeline diagnosis

### Wins

- [project] Zone scanner diagnosis was exhaustive before any conclusion: SSH to Sneg, grep zone_scan.json, check state file, curl tx from node, inspect screenshot — root cause confirmed before recommending action
- [project] Single screenshot (explorer tx detail) revealed two independent bugs simultaneously: wrong channel AND missing #geo tag — both flagged in the reply
- [project] Identified zone_scan_state.json scanned_to=0 side bug (state not written after backward scan) as a separate finding without conflating it with Devisha's issue
- [process] Lessons file updated in the moment when stale data was found (lessons 4, 10, 13) — not deferred to retro

### Fails

- [process] Stated "we compact logs to 12h" in a drafted external reply without reading publish.py first. Moment: asked to research cumulative approach and draft reply to davidrusu. Wrong action: drafted claim from memory, treated assumption as verified fact. Root cause: skipped read-code-first step entirely; Alisher had to prompt the research that should have preceded the draft.

### Project lessons added

- Lesson 4 corrected: compact_logs called with 168h not 12h; default parameter misleads
- Lesson 10 corrected: telemetry retention history clarified (was 12h, now 168h)
- Lesson 13 added: read the code before making any claim about system behavior in external comms

### Fieldcraft update needed

- Process lesson: "verify before communicating externally" belongs in fieldcraft. External replies about system behavior must be based on code reads, not memory. Route to `~/fieldcraft/protocols/` — add to builder-auditor or create a communication protocol.

---

## Epic: Log-Based Peer Discovery (2026-05-19)

### Process wins

- [project] Log discovery proved value immediately on Sneg: 920k lines scanned, 23 log IPs extracted, 5 heard-only peers confirmed (Vietnam, Singapore, Germany, Finland, Brazil) not in crawler DB
- [project] Senty review caught 5 real findings (1 HIGH XSS, 3 MEDIUM, 1 LOW) before merge — all fixed in round 1
- [project] Private IP filter extended from basic RFC1918 to full spec (CGNAT 100.64/10, TEST-NETs, multicast, broadcast) as a direct result of Senty's MEDIUM finding
- [project] `heard_nodes` design (log IPs with geo + not in crawler DB) is the right unit — more precise than raw IP count
- [project] XSS fix used existing `esc()` — zero new code, just consistent application of existing pattern
- [process] Salik patched on-Sneg: model auto-detection, thinking-mode bypass, error handling — now reliably produces review output
- [process] Branch diverge on Sneg resolved cleanly via Python merge script to /tmp (avoiding heredoc shell expansion problems)

### Process fails

- [project] `_LOG_IP_B` regex had wrong segment order (`\d+/\S+` vs correct `\w+/\d+`) — multiaddr is `proto/port` not `port/proto`. Caught by test. Root cause: wrote from memory, didn't verify against a real log line first.
- [project] `UnboundLocalError: bucket_peers` on Sneg — pruning block placed before the variable it depended on. Root cause: inserted code without tracing data flow through the function.
- [project] Test peer IDs contained `0` (invalid base58 char) causing regex failures. Root cause: wrote test strings without checking base58 alphabet.
- [project] Status strip grid showed Heard on second row. Root cause: `repeat(6,...)` not updated to `repeat(7,...)` when adding 7th metric.
- [project] Sneg was 5 commits ahead of remote on main — merge conflict at merge time. Root cause: UI polish done directly on Sneg main without tracking divergence.

### Project lessons added

- Lesson 14: multiaddr format is `proto/port` — verify against real log lines before writing regex
- Lesson 15: Qwen3 thinking mode exhausts token budget silently — bypass via /completion + prefill
- Lesson 16: cache pruning must run after the variable it depends on — trace data flow before inserting into pipelines
- Lesson 17: `heard_count` should equal `len(heard_nodes)`, not count of raw log IPs
- Lesson 18: CSS grid `repeat(N,...)` must match actual child count — update when adding metrics

### Feedback for Alisher

- Sneg UI polish workflow: consider always doing UI work on a branch (even tiny changes) to avoid main diverging from remote unexpectedly


<!-- Template:
## Epic #NN — Title (YYYY-MM-DD)

### Process wins
### Process fails
### Project lessons (added to docs/skills/lessons.md)
### Feedback for Alisher
-->
