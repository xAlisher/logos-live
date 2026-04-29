# Logos Live

A network visualizer for the [Logos](https://logos.co) testnet. Shows peer nodes on a world map, zone-board messages from node runners, stake distribution, and upcoming community events.

Live: **https://xalisher.github.io/logos-live/**

---

## What it shows

- **Overview** — local chain state, peer count, connections, recent block activity, and leader diversity
- **Map** — discovered peers with online/offline status, geo location, ISP, ASN, and inferred infrastructure class
- **Peers** — searchable peer inventory with first/last seen metadata
- **Decentralization** — top countries, cities, ASNs, and residential/hosting/unknown distribution
- **Messages** — on-chain zone-board messages from node runners (`logos:yolo:*` channels), linked to the block explorer
- **Stake** — faucet distribution recipients and block leader activity
- **Telemetry** — active peers by hour, peer uptime, and total stake estimate when logs provide it
- **Setup** — agent-ready node setup links and peer visibility verification
- **Agent** — machine-readable endpoints and Markdown skills for agents inspecting the network or helping set up a node
- **Dev / Community** — GitHub activity and Discourse topics from the Logos ecosystem

---

## Architecture

```
crawler/          Rust — polls /cryptarchia/* every 10 min, writes peers.json + geo_cache.json
zone-scanner/     Rust — scans entire chain history for logos:yolo:* inscriptions, writes zone_scan.json
publish.py        Python — merges all data into network.json, pushes to GitHub Pages
static/index.html Single-file frontend (Leaflet map, vanilla JS)
telemetry_collector.py Python — turns Logos node logs into telemetry.json
pages/            Git worktree — GitHub Pages branch (network.json + index.html)
```

### Crawler (`crawler/src/main.rs`)

Connects directly to the Logos libp2p network through the four bootstrap peers, runs Kademlia discovery, resolves peer IPs via ip-api.com, and persists `peers.json` and `geo_cache.json`. Runs continuously, crawling every 10 minutes.

Current node builds expose `/network/info`, but the tested local node returns `404` for `/network/peers`. Peer rows therefore come from libp2p discovery or a published `network.json` fallback, not from the node HTTP API.

### Zone scanner (`zone-scanner/src/main.rs`)

Walks the entire chain backward from the current tip in 2000-slot batches, extracting opcode=17 inscriptions on `logos:yolo:*` channels. After a full backward pass it polls the tip every 30 seconds for new blocks. Output: `zone_scan.json`.

### Publisher (`publish.py`)

Aggregates peers, geo, events (Luma), GitHub, Discourse, stake distribution, and zone messages into a single `network.json` snapshot, then commits and pushes it to the `pages` branch. Run manually or via cron.

---

## Running locally

### Prerequisites

- Rust (stable)
- Python 3.11+
- A Logos node running at `http://127.0.0.1:8080`

### Install Python deps

```bash
pip install -r requirements.txt
```

For tests:

```bash
pip install -r requirements-dev.txt
pytest -q
```

### Start the local dashboard

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000.

Useful machine-readable endpoints:

- `GET /api/network`
- `GET /api/telemetry`
- `GET /api/agent/manifest`
- `GET /api/agent/state`
- `GET /api/agent/schema`
- `GET /api/agent/bootstrap-peers`
- `GET /api/agent/verify-node/{peer_id}`
- `GET /api/agent/node-visibility/{peer_id}`
- `GET /api/agent/crawler/status`
- `GET /.well-known/logos-live.json`
- `GET /agents/logos-network-skill.md`
- `GET /agents/logos-node-setup-skill.md`

### Which URL to give an agent

For the normal node setup workflow, give the agent the Markdown skill directly:

```
http://127.0.0.1:8000/agents/logos-node-setup-skill.md
```

That is the most portable convention today because most agents can read Markdown instructions from a URL.

For agents or tools that support discovery, give them the well-known entrypoint instead:

```
http://127.0.0.1:8000/.well-known/logos-live.json
```

The well-known endpoint points to the setup skill, inspection skill, manifest, telemetry, schema, and current network state. Structured clients can also go straight to:

```
http://127.0.0.1:8000/api/agent/manifest
```

### Start the peer crawler

```bash
cd crawler
cargo build --release
CRAWL_INTERVAL_SECS=600 ./target/release/logos-crawler &
```

### Generate telemetry from logs

The dashboard reads `telemetry.json` when present. Generate it from Logos node logs:

```bash
python telemetry_collector.py \
  --log-dir ~/logos-blockchain-runbook/state/live-v0.1.2/logs \
  --output telemetry.json
```

The file includes raw peer observations, raw stake events, hourly active peer buckets, peer uptime rows, and total stake estimates.

### Start the zone scanner

```bash
cd zone-scanner
cargo build --release
ZONE_SCAN_FILE=../zone_scan.json ./target/release/zone-scanner &
```

### Publish a snapshot

```bash
python3 publish.py
```

---

## Posting to the map

Node runners can appear on the map by posting zone-board messages.

**1. Run a Logos node**
Use the local agent setup skill:

```
http://127.0.0.1:8000/agents/logos-node-setup-skill.md
```

It contains verified 0.1.2 release assets, current bootstrap peers, and verification steps. The 0.1.2 circuits archive is `v0.4.2`; older `v0.4.1` links are stale.

**2. Announce your location**
Post a message containing `#geo lat,lon` once in your channel. The scanner picks it up and anchors all your future messages to that location.

```
#geo 51.5074,-0.1278
```

**3. Post to the map**
Include `#live` in any zone-board message. It appears as a pinned bubble on the map within ~30 seconds of block finalization.

```
Running well today #live
```

---

## Geo hints

`geo_hints.json` provides a fallback IP→geo mapping for node runners who haven't posted a `#geo` message yet. On-chain announcements always take priority.

```json
{
  "username": "1.2.3.4"
}
```

Open a PR or ping us to add an entry.

---

## Agent setup loop

The intended agent flow is:

1. Read `/.well-known/logos-live.json`.
2. Fetch `/api/agent/manifest`.
3. Fetch `/agents/logos-node-setup-skill.md`.
4. Install and run the Logos node with the current release assets and bootstrap peers.
5. Extract the peer id from `http://localhost:8080/network/info`.
6. Call `/api/agent/verify-node/{peer_id}` until the node is connected, crawler-observed, visible on the map, and represented in telemetry.

`/network/peers` is not part of the current success path because observed node builds return `404` for it. Peer inventory comes from the libp2p crawler and published snapshots.

---

## Data freshness

The published snapshot updates every ~10 minutes via a cron job. Once the project moves to a live-served backend, latency will drop to seconds.
