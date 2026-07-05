# Logos Live — PROJECT_KNOWLEDGE.md

Dashboard file. Keep under 150 lines. Detail lives in `docs/skills/`.

---

## What This Is

Real-time network visualizer and intelligence dashboard for the Logos blockchain testnet.
Displays live peer nodes on a world map, tracks network health, shows zone-board messages,
monitors stake distribution, and exposes agent-ready API endpoints for node setup verification.

Live: https://xalisher.github.io/logos-live/

---

## Current Phase

**Active testnet monitoring — v0.2 chain (node 0.2.0).**
- Migrated the whole pipeline to the Logos v0.2 chain and deployed to Sneg (2026-07-04→06,
  epic #16, issues #17–#25). Node is on `127.0.0.1:8080`; dashboard live at logos.live.
- Key v0.2 shifts: `/cryptarchia/info` nests fields + `mode` object (flatten it); block-by-hash
  is `GET /cryptarchia/blocks/{hash}`; new `lib_slot` finalization anchor; peer telemetry from
  journald (`NODE_LOG_UNIT`); zone-scanner is a parent-hash walk; reqwest→rustls. Full API diff:
  `docs/plans/v0.2-api-diff.md`; decision: `docs/decisions/ADR-0001`.
- DHT kad protocol id and bootstrap peers are UNCHANGED across the fork (verified).
- Log-based peer discovery still shipped: `heard_count`/`heard_nodes` in network.json (journald-sourced on v0.2).
- Next likely work: #25 done; open items below.

---

## Architecture (one-liner per component)

| Component | Role | Language |
|---|---|---|
| `crawler/` | Discovers peers via libp2p Kademlia DHT, writes `peers.json` every 10 min | Rust |
| `zone-scanner/` | Scans chain backward for zone-board inscriptions, writes `zone_scan.json` | Rust |
| `publish.py` | Aggregates all sources → `network.json`, pushes to GitHub Pages hourly; also extracts IP→PeerID from node logs via regex, geo-locates new IPs, computes `heard_count` | Python |
| `server.py` | FastAPI backend, serves static UI + 13 API endpoints, 30s cache | Python |
| `static/index.html` | Leaflet map + telemetry charts + feeds, single-file vanilla JS | JS |
| `pages/` | Git worktree for gh-pages branch (GitHub Pages hosting) | — |

Full data flow and endpoint inventory: `docs/skills/architecture.md`

---

## Machines

| Machine | User | Role |
|---|---|---|
| **Sneg** | `sher` | Runs Logos node (:8080), crawler, zone-scanner, dashboard (:8090), hourly publish cron |
| **Wild** | `alisher` | Dev workstation, local testing |

Cron line on Sneg (runs hourly):
```
0 * * * * /bin/bash -c ". ~/.env.anqa && cd ~/logos-node-visualizer && NODE_URL=http://127.0.0.1:8080 LOG_DIR=/mnt/tc-hdd/logos-node-logs python3 publish.py >> /mnt/tc-hdd/logos-node-logs/publish.log 2>&1"
```

Services on Sneg: `logos-node`, `zone-board`, `dashboard`, `zone-scanner` (all systemd).

Full ecosystem detail: `docs/skills/ecosystem.md`

---

## Open Items / Parked

- [ ] Investigate zone-scanner log volume (rotated archives — may need more aggressive retention)
- [ ] Explore ntfy.sh/Telegram alerting for publish failures (Devon retirement gap)
- [ ] Periodic `git fsck` on Sneg — the repo had corrupt/empty objects (disk-full artifact) that blocked deploys; may recur
- [x] ~~Add `telemetry_cache.json`/`zone_scan_state.json` to `.gitignore`~~ — done (2026-07-04)
- [x] ~~zone_scan_state.json `scanned_to` stays 0~~ — root-caused (pre-fork dead-lock) + fixed by the v0.2 parent-walk scanner + state reset (lesson 24)
- [x] ~~90 stale pre-fork zone_scan.json messages~~ — cleared in the v0.2 cutover (state reset, re-walk from live tip)

---

## Skills Files

| File | When to Read |
|---|---|
| `docs/skills/architecture.md` | Before touching server.py, publish.py, crawler, scanner, or frontend |
| `docs/skills/lessons.md` | When hitting a bug, surprising behavior, or starting any task |
| `docs/skills/ecosystem.md` | Before touching crons, services, external API calls, or machines |

---

## Key Rules (summary — full detail in CLAUDE.md)

- Never edit `zone_scan_state.json` manually
- `pages/` is a git worktree — publish via `publish.py` only
- `NODE_URL` unset = feedback/mock mode; set = live node
- Ask Alisher before: pushing pages, changing crons, touching systemd
