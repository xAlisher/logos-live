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

**Active testnet monitoring — v0.1.x era.**
- Network is live; dashboard is in production use on Sneg
- No active epic in flight (post-maintenance steady state)
- Next likely work: telemetry improvements, zone-scanner log volume investigation

---

## Architecture (one-liner per component)

| Component | Role | Language |
|---|---|---|
| `crawler/` | Discovers peers via libp2p Kademlia DHT, writes `peers.json` every 10 min | Rust |
| `zone-scanner/` | Scans chain backward for zone-board inscriptions, writes `zone_scan.json` | Rust |
| `publish.py` | Aggregates all sources → `network.json`, pushes to GitHub Pages hourly | Python |
| `server.py` | FastAPI backend, serves static UI + 13 API endpoints, 30s cache | Python |
| `static/index.html` | Leaflet map + telemetry charts + feeds, single-file vanilla JS | JS |
| `pages/` | Git worktree for gh-pages branch (GitHub Pages hosting) | — |

Full data flow and endpoint inventory: `docs/skills/architecture.md`

---

## Machines

| Machine | User | Role |
|---|---|---|
| **Sneg** | `sher` | Runs Logos node (:8085), crawler, zone-scanner, dashboard (:8090), hourly publish cron |
| **Wild** | `alisher` | Dev workstation, local testing |

Cron line on Sneg (runs hourly):
```
0 * * * * /bin/bash -c ". ~/.env.anqa && cd ~/logos-node-visualizer && NODE_URL=http://127.0.0.1:8085 LOG_DIR=/mnt/tc-hdd/logos-node-logs python3 publish.py >> /mnt/tc-hdd/logos-node-logs/publish.log 2>&1"
```

Services on Sneg: `logos-node`, `zone-board`, `dashboard`, `zone-scanner` (all systemd).

Full ecosystem detail: `docs/skills/ecosystem.md`

---

## Open Items / Parked

- [ ] Investigate zone-scanner log volume (3 rotated archives — may need more aggressive retention)
- [ ] Consider adding `telemetry_cache.json` and `zone_scan_state.json` to `.gitignore`
- [ ] Explore ntfy.sh/Telegram alerting for publish failures (Devon retirement gap)

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
