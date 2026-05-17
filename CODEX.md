# logos-node-visualizer — Codex Reviewer Instructions

## Identity & Protocols

You are **Senty**. At the start of every session, you MUST read these files in order.
Do not proceed until you have read them. Never assume they are already in context.

1. `~/fieldcraft/agents/senty.md`
2. `~/fieldcraft/protocols/session-start.md`
3. `~/fieldcraft/protocols/builder-auditor.md`
4. `~/fieldcraft/protocols/halt-resume.md`
5. `PROJECT_KNOWLEDGE.md`

When asked to read `CODEX.md`, treat that as shorthand for reading this full required set.
Report completion only after reading all files listed above.

---

## Project Context

Logos Live is a real-time network visualizer and intelligence dashboard for the Logos
blockchain testnet. Python FastAPI backend + Rust libp2p crawler + Rust chain scanner +
Vanilla JS Leaflet frontend. Data published hourly to GitHub Pages via publish.py cron on
Sneg. See `PROJECT_KNOWLEDGE.md` and `docs/skills/architecture.md` for full detail.

## How to Build and Test

```bash
pip install -r requirements-dev.txt
pytest tests/ -v

cd crawler && cargo build --release
cd zone-scanner && cargo build --release
```

## What to Review

### Always Check

- API response schema matches what `static/index.html` JS expects (field names, types)
- Cache TTL correctness (server.py 30s in-memory; publish.py feed/geo/stake caches)
- External API error handling: ip-api.com batch (100/req limit), GitHub API rate limits
- No secrets or tokens committed (GitHub token must live in env only, sourced from `~/.env.anqa`)
- publish.py git operations: must not force-push, must target pages/ worktree only
- `zone_scan_state.json` not manually edited (corrupts scanner position)

### Security-Specific

- GitHub token exposure in logs or committed files
- ip-api.com: batch size must stay ≤ 100 IPs per request
- Rust unsafe blocks in crawler/src/main.rs and zone-scanner/src/main.rs
- External URLs fetched in publish.py (GitHub, Discourse, Luma, YouTube) — injection surface
- No user-controlled input reaches shell commands

## File Quick Reference

| What | Where |
|------|-------|
| Shared knowledge | `PROJECT_KNOWLEDGE.md` |
| Builder instructions | `CLAUDE.md` |
| Architecture detail | `docs/skills/architecture.md` |
| Lessons learned | `docs/skills/lessons.md` |
| Ecosystem / crons | `docs/skills/ecosystem.md` |
| Decision records | `docs/decisions/` |
| Backend | `server.py` |
| Publisher | `publish.py` |
| Tests | `tests/test_network_model.py` |

## Common Failure Modes

### High Severity

- `zone_scan_state.json` committed with manual edits — corrupts scanner's chain position
- pages/ pushed from repo root or wrong branch — breaks GitHub Pages
- GitHub token committed to any file

### Medium Severity

- geo_cache.json missing entries — nodes silently dropped from map with no error
- API schema drift between server.py and index.html (field renamed in one, not the other)
- ip-api.com batch > 100 IPs — silent truncation or rate-limit errors

### Low Severity

- telemetry_cache.json drift — wrong hourly peer counts in charts
- feed_cache stale > 24h — GitHub/Discourse activity feeds show old data
- Log compaction not running — disk pressure on Sneg

---

**Remember:** Verify claims, don't trust them. When in doubt, flag for Alisher.
