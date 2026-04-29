# Logos Live

A real-time network visualizer for the [Logos](https://logos.co) testnet. Shows peer nodes on a world map, zone-board messages from node runners, stake distribution, and upcoming community events.

Live: **https://xalisher.github.io/logos-live/**

---

## What it shows

- **Map** — all discovered peers with online/offline status, geo location, ISP
- **Messages** — on-chain zone-board messages from node runners (`logos:yolo:*` channels), linked to the block explorer
- **Stake** — faucet distribution recipients and block leader activity
- **Dev / Community** — GitHub activity and Discourse topics from the Logos ecosystem

---

## Architecture

```
crawler/          Rust — polls /cryptarchia/* every 10 min, writes peers.json + geo_cache.json
zone-scanner/     Rust — scans entire chain history for logos:yolo:* inscriptions, writes zone_scan.json
publish.py        Python — merges all data into network.json, pushes to GitHub Pages
static/index.html Single-file frontend (Leaflet map, vanilla JS)
pages/            Git worktree — GitHub Pages branch (network.json + index.html)
```

### Crawler (`crawler/src/main.rs`)

Connects to a local Logos node at `http://127.0.0.1:8080`, walks `/cryptarchia/blocks` and `/network/peers`, resolves peer IPs via ip-api.com, and persists `peers.json` and `geo_cache.json`. Runs continuously, crawling every 10 minutes.

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

### Start the peer crawler

```bash
cd crawler
cargo build --release
CRAWL_INTERVAL_SECS=600 ./target/release/logos-crawler &
```

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
Follow the setup guide at https://github.com/logos-co/nomos-node

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

## Data freshness

The published snapshot updates every ~10 minutes via a cron job. Once the project moves to a live-served backend, latency will drop to seconds.
