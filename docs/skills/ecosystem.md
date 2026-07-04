# Ecosystem — docs/skills/ecosystem.md

External dependencies, machines, services, and scheduled jobs.

---

## External APIs

| API | Usage | Limits / Notes |
|---|---|---|
| `ip-api.com/batch` | IP geolocation for peer nodes | 100 IPs/request, no API key required, results cached in `geo_cache.json` |
| `api.github.com/orgs/logos-co/events` | GitHub ecosystem activity feed | Requires GitHub token (in `~/.env.anqa`), rate-limited |
| `forum.logos.co/latest.json` | Discourse community forum topics | Public endpoint, no auth |
| `luma.com` (calendar API) | Upcoming Logos community events | Public |
| YouTube via `yt-dlp` | Latest Logos videos | No API key; yt-dlp must be installed on machine running publish.py |

---

## Machines

| Machine | Hostname | User | Role |
|---|---|---|---|
| **Sneg** | `sneg` | `sher` | Production: Logos node, crawler, zone-scanner, dashboard, publish cron |
| **Wild** | `wild` | `alisher` | Development workstation, local testing only |

---

## Systemd Services (on Sneg)

| Service | Binary / Script | Port | Notes |
|---|---|---|---|
| `logos-node.service` | `artifacts/node/logos-blockchain-node` | 8085 | Logos blockchain node |
| `zone-board.service` | `artifacts/zone-sdk-test-v0.2.2/zone-board` | — | Zone-board runner |
| `dashboard.service` | `dashboard/server.py` | 8090 | Web dashboard (FastAPI) |
| `zone-scanner.service` | `zone-scanner/target/release/zone-scanner` | — | Chain inscription scanner |

To check service status: `systemctl status <service-name>`
To restart: `sudo systemctl restart <service-name>` (requires Alisher approval for restarts in prod)

---

## Cron Jobs

### Sneg (sher) — Hourly Publish

```
0 * * * * /bin/bash -c ". /home/sher/.env.anqa && cd /home/sher/logos-node-visualizer && NODE_URL=http://127.0.0.1:8080 LOG_DIR=/mnt/tc-hdd/logos-node-logs python3 publish.py >> /mnt/tc-hdd/logos-node-logs/publish.log 2>&1"
```

- Sources secrets from `~/.env.anqa` before running
- Output logged to `publish.log` in `LOG_DIR`
- Pushes to `pages/` worktree → GitHub Pages

### Sneg (sher) — Daily Log Rotation

```
0 2 * * * logrotate ~/.config/logrotate/logos-visualizer
```

- Runs at 02:00 UTC daily
- Rotates `crawler.log` and `zone-scanner.log`
- 7-day retention, gzip compression
- Separate from the 12h node log compaction inside `publish.py`

---

## Log Locations

| Log | Location | Managed by |
|---|---|---|
| `crawler.log` | Repo root | logrotate (7-day) |
| `zone-scanner.log` | Repo root | logrotate (7-day) |
| `publish.log` | `LOG_DIR` (`/mnt/tc-hdd/logos-node-logs/`) | Not rotated (append only) |
| Node logs | `LOG_DIR` | publish.py (12h compaction) |

---

## GitHub Pages

- Branch: `gh-pages`
- Managed via `pages/` git worktree
- Updated by: `publish.py` on every hourly run
- URL: https://xalisher.github.io/logos-live/
- Contains: `network.json`, `index.html`, `logo.svg`, `social.png`, `CNAME`, `.nojekyll`

---

## Python Dependencies

```
fastapi==0.115.12
uvicorn==0.34.0
httpx==0.28.1
pytest==9.0.3   # dev only
```

## Rust Dependencies (key crates)

| Crate | Used in | Purpose |
|---|---|---|
| `libp2p 0.55` | crawler | P2P networking + Kademlia DHT |
| `tokio` | both | Async runtime |
| `reqwest 0.12` | zone-scanner | HTTP client for node API calls |
| `serde / serde_json` | both | JSON serialization |
| `anyhow` | both | Error handling |
