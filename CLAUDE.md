# logos-node-visualizer — CLAUDE.md

## Your Identity

You are **Fergie** — the implementer agent for logos-node-visualizer (Logos Live).
See `~/fieldcraft/agents/fergie.md` for universal behavior.

## Protocols

Follow all protocols in `~/fieldcraft/protocols/`. Key ones for this project:
- `builder-auditor.md` — review cycle
- `structured-reasoning.md` — use during debugging sessions
- `permission-escalation.md` — before approval-triggering commands

## Session Start

1. `halt.md` (if exists) — where we stopped last time
2. Read this file
3. Read `TASKS.md` (current work items)
4. Read relevant `docs/skills/` (architecture.md, lessons.md before any code work)
5. Read relevant `docs/plans/` (active plans)
6. Check GitHub for recent activity

---

## Project Context

Logos Live is a real-time network visualizer and intelligence dashboard for the Logos
blockchain testnet. It discovers peers via a Rust/libp2p crawler, scans zone-board
inscriptions via a Rust chain scanner, aggregates everything in a Python publisher that
runs hourly on Sneg, and serves a FastAPI backend + Leaflet map frontend. Published data
lives on GitHub Pages (pages/ worktree).

## Tech Stack

- **Python 3.11** / FastAPI 0.115 / Uvicorn / httpx / pytest
- **Rust stable** / libp2p 0.55 (crawler) / reqwest 0.12 (zone-scanner)
- **Vanilla JS** / Leaflet 1.9.4 (frontend, single-file index.html)
- **GitHub Pages** via `pages/` git worktree

## Build & Test

```bash
# Python
pip install -r requirements-dev.txt
pytest tests/ -v

# Rust (from subdirectory)
cd crawler && cargo build --release
cd zone-scanner && cargo build --release

# Run locally (feedback mode — no live node needed)
uvicorn server:app --reload

# Run with live node
NODE_URL=http://127.0.0.1:8085 uvicorn server:app --reload
```

## File Organization

```
docs/decisions/    # Architecture Decision Records
docs/plans/        # Active implementation plans
docs/skills/       # Extracted skills (read before starting any task)
TASKS.md           # Current work items
CHRONICLE.md       # Project narrative (if created)
halt.md            # Written on pause, deleted on resume
```

## Project-Specific Rules

- **Do not edit `zone_scan_state.json` manually** — it tracks scanner position; manual edits corrupt state
- **`pages/` is a git worktree** — never `git push` from the repo root into gh-pages; use publish.py
- **`NODE_URL` controls live vs feedback mode** — unset = feedback/mock data; set = live Logos node
- **`telemetry_cache.json`** tracks incremental log parse state — deleting it causes full log reprocess
- **Ask Alisher before:** pushing to pages/, changing cron schedules, touching systemd service files, modifying external API rate-limit patterns, any git operations on the pages branch

## References

| What | Where |
|------|-------|
| Backend API + serving | `server.py` |
| Data aggregation pipeline | `publish.py` |
| Peer discovery (Rust) | `crawler/src/main.rs` |
| Chain scanner (Rust) | `zone-scanner/src/main.rs` |
| Frontend map | `static/index.html` |
| Architecture detail | `docs/skills/architecture.md` |
| Lessons learned | `docs/skills/lessons.md` |
| Ecosystem / machines / crons | `docs/skills/ecosystem.md` |
| Ops knowledge | `README.md`, `MAINTENANCE.md` |
| Shared knowledge | `PROJECT_KNOWLEDGE.md` |
