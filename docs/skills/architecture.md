# Architecture — docs/skills/architecture.md

## Component Roles

| File | Lines | Purpose |
|---|---|---|
| `server.py` | 1,456 | FastAPI app. Reads cached JSON files, queries local Logos node at `NODE_URL`, serves 13 API endpoints + static HTML. 30s in-memory cache per endpoint. |
| `publish.py` | 1,257 | Hourly aggregation pipeline. Merges crawler peers + geo + chain + GitHub + Discourse + Luma + YouTube → `network.json`. Commits + pushes to `pages/` worktree. Also compacts node logs (12h retention). |
| `telemetry_collector.py` | 64 | Standalone CLI. Parses node logs for peer observations and stake events → `telemetry.json`. |
| `crawler/src/main.rs` | ~300 | libp2p Kademlia crawler. Dials 4 bootstrap peers, runs FIND_NODE queries, writes `peers.json` every 10 min. |
| `zone-scanner/src/main.rs` | ~400 | Blockchain walker. Scans chain backward in 2k-slot batches for opcode=17 inscriptions on `logos:yolo:*`. Writes `zone_scan.json`. Polls tip every 30s after catch-up. |
| `static/index.html` | 82KB | Single-file Leaflet map + telemetry charts + feeds. Fetches `/api/network`, renders map pins, peer tables, activity feeds. Dark/light theme. |
| `pages/` | — | Git worktree for `gh-pages` branch. Contains `network.json` + `index.html` + assets. Published to xalisher.github.io/logos-live/. |

---

## Data Flow

```
Logos node (:8080)     ip-api.com          GitHub API    Discourse    Luma    YouTube
      ↓                    ↓                    ↓            ↓          ↓        ↓
      └──────────────────────────────────────────────────────────────────────────┘
                                      publish.py (hourly cron)
                                           ↓
                   reads: peers.json, geo_cache.json, zone_scan.json
                   writes: network.json → pages/ → git push → GitHub Pages
                                           ↓
                                      server.py
                              reads all cache files + calls
                              NODE_URL for live chain state
                                           ↓
                              /api/network (30s cache)
                                           ↓
                               static/index.html (Leaflet)
```

---

## Runtime Cache Files

| File | Written by | Read by | Notes |
|---|---|---|---|
| `peers.json` | crawler (every 10 min) | publish.py, server.py | Discovered peer multiaddrs |
| `geo_cache.json` | publish.py | publish.py, server.py | IP → lat/lon from ip-api.com |
| `geo_hints.json` | manual | publish.py | Fallback IP → location for stubborn IPs |
| `zone_scan.json` | zone-scanner | publish.py, server.py | Zone-board messages |
| `zone_scan_state.json` | zone-scanner | zone-scanner | Scanner chain position — **never edit manually** |
| `stake_cache.json` | publish.py | publish.py | Faucet recipients + leaders |
| `feed_cache.json` | publish.py | publish.py | GitHub/Discourse/YouTube feed cache |
| `telemetry_cache.json` | publish.py | publish.py | Incremental log parse position |
| `telemetry.json` | telemetry_collector.py | server.py | Hourly peer counts + uptime |

---

## API Endpoints (13 total)

| Endpoint | Purpose |
|---|---|
| `GET /api/network` | Full dashboard payload (nodes, map, telemetry, chain state, feeds) |
| `GET /api/telemetry` | Telemetry object only (hourly active peers, uptime, stake) |
| `GET /api/agent/manifest` | Machine-readable agent contract |
| `GET /api/agent/state` | Compact agent state (chain, network, summary, nodes, recent blocks) |
| `GET /api/agent/schema` | Describes all endpoints and purposes |
| `GET /api/agent/bootstrap-peers` | Current bootstrap peer multiaddrs |
| `GET /api/agent/crawler/status` | Crawler health (file exists, last crawl, node count, freshness) |
| `GET /api/agent/verify-node/{peer_id}` | 6-stage node verification checklist |
| `GET /api/agent/node-visibility/{peer_id}` | Simple visibility report + next actions |
| `GET /.well-known/logos-live.json` | Discovery entrypoint (manifest, API, skills, capabilities) |
| `GET /agents/logos-network-skill.md` | Markdown skill for agents inspecting the network |
| `GET /agents/logos-node-setup-skill.md` | Markdown skill for agents helping users set up a node |
| `GET /static/*` | Static assets (index.html, logo.svg, social.png) |

---

## Caching Layers

- **server.py**: 30s in-memory cache per endpoint (TTL via timestamp check)
- **publish.py feed_cache**: GitHub/Discourse/YouTube — persisted to `feed_cache.json`
- **publish.py geo_cache**: IP → location — persisted to `geo_cache.json` (never expires, grows only)
- **publish.py telemetry_cache**: incremental log parse position — persisted to `telemetry_cache.json`
- **publish.py stake_cache**: faucet data — persisted to `stake_cache.json`

---

## pages/ Worktree Mechanics

`pages/` is a separate git worktree tracking the `gh-pages` branch:

```bash
# How it was set up (once):
git worktree add pages gh-pages

# How publish.py uses it:
# 1. Writes network.json into pages/
# 2. cd pages && git add network.json && git commit -m "..." && git push
```

Never run `git push` from the repo root targeting gh-pages. Always use publish.py.

---

## Logging

- `crawler.log` — crawler stderr (rotated daily, 7-day retention via logrotate on Sneg)
- `zone-scanner.log` — scanner stderr (same rotation)
- `publish.log` — hourly cron output (at `LOG_DIR` on Sneg)
- Node logs at `LOG_DIR` (`/mnt/tc-hdd/logos-node-logs/` on Sneg) — **compacted to 12h by publish.py**
