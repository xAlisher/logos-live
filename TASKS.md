# TASKS.md

Current work items. Status: `[ ]` todo, `[x]` done, `[~]` in progress, `[!]` blocked.

---

## Active

_(none — v0.2 migration complete + deployed)_

---

## Backlog

- [ ] #25 — rework log-based telemetry parsing (heard peers) for the v0.2 node log format (`MyLogFile.log`, not `logos-blockchain.*`)
- [ ] Investigate zone-scanner log accumulation (rotated `.gz` archives — check volume vs retention policy)
- [ ] Explore failure alerting for publish.py cron (ntfy.sh or Telegram — fills Devon retirement gap)
- [ ] Review `debug/node_modules/` — appears unused, consider removing

---

## Done

- [x] **v0.2 chain migration** (epic #16, issues #17–#24) — crawler bootstrap, zone-scanner rewrite (parent-hash walk), publish.py + server.py chain reads, frontend, docs. Deployed to Sneg + published to logos.live. 2026-07-04.
- [x] Add runtime state to `.gitignore` (`zone_scan_state.json`, `telemetry_cache.json`, rotated logs)

---

## Done

- [x] Fieldcraft retrofit — CLAUDE.md, CODEX.md, PROJECT_KNOWLEDGE.md, docs/skills/, retro infra
