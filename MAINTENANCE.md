# Maintenance — Scheduled Jobs & Log Compaction

## Cron Jobs

| Schedule | Command | Purpose |
|----------|---------|---------|
| Every hour (`0 * * * *`) | `python3 publish.py` | Build network.json and push to GitHub Pages |
| Daily 06:00 (`0 6 * * *`) | `discord-refresh.sh && morning-update.sh` | Basecamp morning update |
| Daily 02:00 (`0 2 * * *`) | `logrotate` (see below) | Rotate and compress log files |

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
- Keeps last **12 hours** of logs from `state/live-v0.1.2/logs/`
- Older files are deleted automatically

## Systemd Services (auto-start on boot)

| Service | Binary | Purpose |
|---------|--------|---------|
| `logos-node.service` | `artifacts/node/logos-blockchain-node` | Logos blockchain node |
| `zone-board.service` | `artifacts/zone-sdk-test-v0.2.2/zone-board` | Zone board (tmux) |
| `dashboard.service` | `dashboard/server.py` | Web dashboard on :8090 |
| `zone-scanner.service` | `zone-scanner/target/release/zone-scanner` | Scans chain for #live messages |
