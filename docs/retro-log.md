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

<!-- Template:
## Epic #NN — Title (YYYY-MM-DD)

### Process wins
### Process fails
### Project lessons (added to docs/skills/lessons.md)
### Feedback for Alisher
-->
