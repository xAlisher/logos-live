# Maintenance — Scheduled Jobs & Log Compaction

## Cron Jobs

**publish.py runs on Sneg** (not Wild) — logos-node migrated to Sneg (May 2026).
`NODE_URL` must be `http://127.0.0.1:8080`. Cron uses `/bin/bash -c` because `source` fails in `/bin/sh`.

| Machine | Schedule | Command | Purpose |
|---------|----------|---------|---------|
| Sneg (`sher`) | Every hour (`0 * * * *`) | `NODE_URL=http://127.0.0.1:8080 LOG_DIR=/mnt/tc-hdd/logos-node-logs python3 publish.py` | Build network.json and push to GitHub Pages |
| Wild (`alisher`) | Daily 06:00 (`0 6 * * *`) | `discord-refresh.sh && morning-update.sh` | Basecamp morning update |
| Sneg (`sher`) | Daily 02:00 (`0 2 * * *`) | `logrotate` (see below) | Rotate and compress log files |

Full cron line on Sneg:
```
0 * * * * /bin/bash -c ". /home/sher/.env.anqa && cd /home/sher/logos-node-visualizer && NODE_URL=http://127.0.0.1:8080 LOG_DIR=/mnt/tc-hdd/logos-node-logs python3 publish.py >> /mnt/tc-hdd/logos-node-logs/publish.log 2>&1"
```

## Log Rotation (logrotate)

Config: `~/.config/logrotate/logos-visualizer`
State:  `~/.config/logrotate/state`

Managed logs (daily, 7-day retention, gzip compressed):

| Log file | Process |
|----------|---------|
| `zone-scanner.log` | zone-scanner systemd service |
| `crawler.log` | crawler process |
| `~/.../zone-board-v0.2.2/zone-board.log` | zone-board systemd service |

Uses `copytruncate` — no process restart needed on rotation.

## Node Log Compaction

`publish.py` compacts node logs automatically on every run:
- Keeps last **12 hours** of logs from `logos-v2/standalone/logs/`
- Older files are deleted automatically

## Systemd Services (auto-start on boot)

| Service | Binary | Purpose |
|---------|--------|---------|
| `logos-node.service` | `artifacts/node/logos-blockchain-node` | Logos blockchain node |
| `zone-board.service` | `artifacts/zone-sdk-test-v0.2.2/zone-board` | Zone board (tmux) |
| `dashboard.service` | `dashboard/server.py` | Web dashboard on :8090 |
| `zone-scanner.service` | `zone-scanner/target/release/zone-scanner` | Scans chain for #live messages |
