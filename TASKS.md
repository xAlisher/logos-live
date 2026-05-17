# TASKS.md

Current work items. Status: `[ ]` todo, `[x]` done, `[~]` in progress, `[!]` blocked.

---

## Active

_(none — steady state post-fieldcraft-retrofit)_

---

## Backlog

- [ ] Investigate zone-scanner log accumulation (3 rotated `.gz` archives — check volume vs retention policy)
- [ ] Add `telemetry_cache.json` and `zone_scan_state.json` to `.gitignore` (runtime state, not source)
- [ ] Explore failure alerting for publish.py cron (ntfy.sh or Telegram — fills Devon retirement gap)
- [ ] Review `debug/node_modules/` — appears unused, consider removing

---

## Done

- [x] Fieldcraft retrofit — CLAUDE.md, CODEX.md, PROJECT_KNOWLEDGE.md, docs/skills/, retro infra
