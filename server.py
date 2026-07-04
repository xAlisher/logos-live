"""
Logos Network Visualizer — backend
Reads peer IPs from node logs + Kademlia DHT entries, geolocates them,
serves data to the frontend map.
"""

import asyncio
import json
import os
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

# ── Config (override via env vars) ────────────────────────────────────────────
NODE_URL = os.getenv("NODE_URL", "http://127.0.0.1:8080")
LOG_DIR  = os.getenv(
    "LOG_DIR",
    os.path.expanduser("~/logos-blockchain-runbook/state/live-v0.1.2/logs"),
)
CACHE_TTL = int(os.getenv("CACHE_TTL", "30"))  # seconds
PUBLISHED_NETWORK_URL = os.getenv(
    "PUBLISHED_NETWORK_URL",
    "https://xalisher.github.io/logos-live/network.json",
)
TELEMETRY_FILE = os.getenv("TELEMETRY_FILE", os.path.join(os.path.dirname(__file__), "telemetry.json"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
LOGOS_NODE_VERSION = "0.1.2"
LOGOS_CIRCUITS_VERSION = "0.4.2"
LOGOS_RELEASE_URL = "https://github.com/logos-blockchain/logos-blockchain/releases/tag/0.1.2"
LOGOS_RELEASE_DOWNLOAD_BASE = "https://github.com/logos-blockchain/logos-blockchain/releases/download/0.1.2"

RELEASE_ASSETS = {
    "node": {
        "linux-x86_64": f"{LOGOS_RELEASE_DOWNLOAD_BASE}/logos-blockchain-node-linux-x86_64-{LOGOS_NODE_VERSION}.tar.gz",
        "linux-aarch64": f"{LOGOS_RELEASE_DOWNLOAD_BASE}/logos-blockchain-node-linux-aarch64-{LOGOS_NODE_VERSION}.tar.gz",
        "macos-aarch64": f"{LOGOS_RELEASE_DOWNLOAD_BASE}/logos-blockchain-node-macos-aarch64-{LOGOS_NODE_VERSION}.tar.gz",
    },
    "circuits": {
        "linux-x86_64": f"{LOGOS_RELEASE_DOWNLOAD_BASE}/logos-blockchain-circuits-v{LOGOS_CIRCUITS_VERSION}-linux-x86_64.tar.gz",
        "linux-aarch64": f"{LOGOS_RELEASE_DOWNLOAD_BASE}/logos-blockchain-circuits-v{LOGOS_CIRCUITS_VERSION}-linux-aarch64.tar.gz",
        "macos-aarch64": f"{LOGOS_RELEASE_DOWNLOAD_BASE}/logos-blockchain-circuits-v{LOGOS_CIRCUITS_VERSION}-macos-aarch64.tar.gz",
    },
}

# Source of truth: crawler/src/main.rs `BOOTSTRAP`. Keep these peer IDs in sync
# with it. These four were verified live against the v0.2 node's
# /network/info `connected_peers` on 2026-07-04 (the prior IDs here were stale
# and appeared in no live peer set). ip/port unchanged across the v0.1.2→v0.2
# fork; the DHT also kept the `/logos-blockchain-testnet-v0.1.2/kad/1.0.0`
# protocol id (crawler finds peers with no KAD_PROTOCOL override).
BOOTSTRAP_PEERS = [
    {
        "name": "bootstrap-3000",
        "ip": "65.109.51.37",
        "udp_port": 3000,
        "peer_id": "12D3KooWFrouXfmrR4nsLMtE7wu15DoMJ6VtoUtHinREZCvbWHar",
    },
    {
        "name": "bootstrap-3001",
        "ip": "65.109.51.37",
        "udp_port": 3001,
        "peer_id": "12D3KooWJRGau8M1rjT7R5e4YYsgdFhsMX35nRDtMwCDjxQkXAHz",
    },
    {
        "name": "bootstrap-3002",
        "ip": "65.109.51.37",
        "udp_port": 3002,
        "peer_id": "12D3KooWQXJavMDTRscjauFSgVAB1VLB6Rzpy2uY5SU9Tk7927tb",
    },
    {
        "name": "bootstrap-3003",
        "ip": "65.109.51.37",
        "udp_port": 3003,
        "peer_id": "12D3KooWSQc7CcGtvWDPF1yCbBthFnQjprfCVHmfmNDUrSmqQsU1",
    },
]
for peer in BOOTSTRAP_PEERS:
    peer["multiaddr"] = f"/ip4/{peer['ip']}/udp/{peer['udp_port']}/quic-v1/p2p/{peer['peer_id']}"

# ── Compiled patterns ──────────────────────────────────────────────────────────
_ANSI      = re.compile(r"\x1b\[[0-9;]*m")
_PEER_ADDR = re.compile(
    r"Added address /ip4/(\d+\.\d+\.\d+\.\d+)/\S+ to peer PeerId\(\"([^\"]+)\"\)"
)
_PEER_ID = re.compile(r"\b(12D3KooW[1-9A-HJ-NP-Za-km-z]{20,})\b")
PEER_ID_RE = re.compile(r"^12D3KooW[1-9A-HJ-NP-Za-km-z]{20,}$")
_ISO_TS = re.compile(r"\b(\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d:[0-5]\d(?:\.\d+)?Z?)\b")
_STAKE_VALUE = re.compile(
    r"\b(?:old_total_stake|total_stake|active_stake|stake_estimate|tsi)\s*[=:]\s*([0-9][0-9_,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict | None = None
_cache_ts: float = 0.0
_cache_lock = asyncio.Lock()


def _is_public(ip: str) -> bool:
    try:
        p = list(map(int, ip.split(".")))
    except ValueError:
        return False
    if len(p) != 4 or any(part < 0 or part > 255 for part in p):
        return False
    if p[0] == 10:                              return False
    if p[0] == 172 and 16 <= p[1] <= 31:       return False
    if p[0] == 192 and p[1] == 168:            return False
    if p[0] == 127:                             return False
    if p[0] == 100 and 64 <= p[1] <= 127:      return False  # CGNAT
    if p[0] >= 224:                             return False  # multicast/reserved
    return True


def validate_peer_id(peer_id: str) -> str:
    if not PEER_ID_RE.match(peer_id):
        raise HTTPException(status_code=400, detail="Invalid peer ID format")
    return peer_id


PEERS_FILE = os.getenv("PEERS_FILE", os.path.join(os.path.dirname(__file__), "peers.json"))


HOSTING_TERMS = (
    "amazon", "aws", "google cloud", "microsoft", "azure", "oracle", "digitalocean",
    "linode", "akamai", "hetzner", "ovh", "contabo", "netcup", "vultr", "leaseweb",
    "scaleway", "rackspace", "data center", "datacenter", "hosting", "server",
    "cloud", "colo", "colocation", "xTom", "mevspace",
)

RESIDENTIAL_TERMS = (
    "telefonica", "telecom", "communications", "comunicaciones", "broadband",
    "cable", "dsl", "fiber", "fibra", "mobile", "wireless", "residential",
    "retail", "vocus retail", "telia", "orange", "vodafone", "comcast",
    "charter", "verizon", "at&t", "cox", "spectrum",
)


def classify_node_environment(node: dict[str, Any]) -> str:
    """Best-effort IP network classification from public ASN/ISP metadata."""
    text = " ".join(
        str(node.get(k, "") or "").lower()
        for k in ("isp", "org", "asn")
    )
    if not text.strip():
        return "Unknown"
    if any(term.lower() in text for term in HOSTING_TERMS):
        return "Hosting"
    if any(term.lower() in text for term in RESIDENTIAL_TERMS):
        return "Residential"
    return "Unknown"


def _pct(part: int | float, total: int | float) -> float:
    return round((part / total) * 100, 2) if total else 0.0


def _base_url(request: Request | None = None) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    if request:
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8000"


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def build_bootstrap_peers() -> list[dict[str, Any]]:
    return [dict(peer) for peer in BOOTSTRAP_PEERS]


def build_agent_manifest(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    return {
        "name": "Logos Live Agent Manifest",
        "network": "Logos testnet",
        "skill_version": "1.0.0",
        "capabilities": [
            "network_inspection",
            "telemetry",
            "node_setup_guidance",
            "node_visibility_verification",
        ],
        "release": {
            "node_version": LOGOS_NODE_VERSION,
            "circuits_version": LOGOS_CIRCUITS_VERSION,
            "release_url": LOGOS_RELEASE_URL,
            "assets": RELEASE_ASSETS,
        },
        "bootstrap_peers": build_bootstrap_peers(),
        "defaults": {
            "node_api_url": NODE_URL,
            "node_api_port": 8080,
            "p2p_udp_port": 3000,
            "circuits_dir": "~/.logos-blockchain-circuits",
        },
        "endpoints": {
            "network": {"url": _url(base, "/api/network")},
            "agent_state": {"url": _url(base, "/api/agent/state")},
            "telemetry": {"url": _url(base, "/api/telemetry")},
            "bootstrap_peers": {"url": _url(base, "/api/agent/bootstrap-peers")},
            "crawler_status": {"url": _url(base, "/api/agent/crawler/status")},
            "visibility": {"url_template": _url(base, "/api/agent/node-visibility/{peer_id}")},
            "verify_node": {"url_template": _url(base, "/api/agent/verify-node/{peer_id}")},
            "inspect_skill": {"url": _url(base, "/agents/logos-network-skill.md")},
            "setup_skill": {"url": _url(base, "/agents/logos-node-setup-skill.md")},
            "well_known": {"url": _url(base, "/.well-known/logos-live.json")},
        },
        "limitations": [
            "/network/peers currently returns 404 on observed node builds; use libp2p crawler observations for peer rows.",
            "Public map visibility requires the crawler to observe the peer or the node runner to publish a #geo/#live zone message.",
            "Residential versus hosting classification is inferred from public ASN/ISP metadata.",
        ],
    }


def _find_node(peer_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    for node in data.get("nodes") or []:
        if node.get("peer_id") == peer_id:
            return node
    return None


def _find_uptime(peer_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    telemetry = data.get("telemetry") or {}
    for peer in telemetry.get("peer_uptime") or []:
        if peer.get("peer_id") == peer_id:
            return peer
    return None


def build_node_visibility(peer_id: str, data: dict[str, Any]) -> dict[str, Any]:
    node = _find_node(peer_id, data)
    uptime = _find_uptime(peer_id, data)
    location = ""
    if node:
        location = ", ".join(part for part in (node.get("city"), node.get("country")) if part)
    visible = node is not None
    next_actions: list[str] = []
    if not visible:
        next_actions.append("Wait for the crawler to observe this peer, then refresh the dashboard.")
        next_actions.append("Confirm the node is reachable from bootstrap peers and reports nonzero peers in /network/info.")
        next_actions.append("Optionally post a #geo lat,lon and #live zone message so the map can anchor the node manually.")
    elif not location:
        next_actions.append("Add a #geo lat,lon zone message or a geo_hints.json entry to improve map placement.")
    return {
        "peer_id": peer_id,
        "visible_on_map": visible,
        "observed_in_telemetry": uptime is not None,
        "node": node,
        "uptime": uptime,
        "location": location or None,
        "next_actions": next_actions,
    }


def _stage(stage_id: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": stage_id, "label": label, "status": status, "detail": detail}


def build_node_verification(peer_id: str, data: dict[str, Any]) -> dict[str, Any]:
    chain = data.get("chain") or {}
    network = data.get("network") or {}
    visibility = build_node_visibility(peer_id, data)
    local_peer_id = network.get("peer_id")
    n_peers = int(network.get("n_peers") or 0)
    n_connections = int(network.get("n_connections") or 0)
    mode = chain.get("mode") or "Unknown"

    stages = [
        _stage(
            "node_api",
            "Node API",
            "passed" if local_peer_id else "failed",
            f"Local API reports peer id {local_peer_id}" if local_peer_id else "Local /network/info did not return a peer id.",
        ),
        _stage(
            "consensus_online",
            "Consensus mode",
            "passed" if mode == "Online" else "warning" if mode == "Bootstrapping" else "failed",
            f"Consensus mode is {mode}.",
        ),
        _stage(
            "peer_connectivity",
            "Peer connectivity",
            "passed" if n_peers > 0 and n_connections > 0 else "failed",
            f"/network/info reports {n_peers} peers and {n_connections} connections.",
        ),
        _stage(
            "crawler_observed",
            "Crawler observed peer",
            "passed" if visibility["node"] else "warning",
            "Peer is present in the map node set." if visibility["node"] else "Peer has not appeared in crawler or published map data yet.",
        ),
        _stage(
            "map_visible",
            "Map visibility",
            "passed" if visibility["visible_on_map"] else "warning",
            f"Visible at {visibility['location']}." if visibility["location"] else "Peer is not geolocated on the map yet.",
        ),
        _stage(
            "telemetry",
            "Telemetry",
            "passed" if visibility["observed_in_telemetry"] else "warning",
            "Peer has uptime telemetry." if visibility["observed_in_telemetry"] else "No uptime telemetry for this peer yet.",
        ),
    ]
    required = {"node_api", "consensus_online", "peer_connectivity"}
    failed_required = [stage for stage in stages if stage["id"] in required and stage["status"] != "passed"]
    next_actions = list(visibility["next_actions"])
    if mode == "Bootstrapping":
        next_actions.insert(0, "Wait for the node to finish bootstrapping before expecting consensus participation.")
    if n_peers == 0 or n_connections == 0:
        next_actions.insert(0, "Re-run init with the current bootstrap peers and confirm UDP connectivity to port 3000.")
    return {
        "peer_id": peer_id,
        "local_peer_id": local_peer_id,
        "is_local_node": peer_id == local_peer_id,
        "overall_status": "ready" if not failed_required else "pending",
        "stages": stages,
        "visibility": visibility,
        "next_actions": next_actions,
    }


def build_crawler_status(now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    path = Path(PEERS_FILE).expanduser()
    status: dict[str, Any] = {
        "peers_file": str(path),
        "peers_file_exists": path.exists(),
        "crawler_observations": 0,
        "last_crawl": None,
        "last_crawl_age_seconds": None,
        "healthy": False,
        "notes": [],
    }
    if not path.exists():
        status["notes"].append("No peers.json file exists yet. Start the crawler to populate map peer rows.")
        return status
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        status["notes"].append("peers.json exists but could not be read as JSON.")
        return status
    last_crawl = payload.get("last_crawl")
    nodes = payload.get("nodes") or {}
    status["crawler_observations"] = len(nodes)
    status["last_crawl"] = last_crawl
    if last_crawl:
        status["last_crawl_age_seconds"] = max(now - int(last_crawl), 0)
    status["healthy"] = bool(nodes) and (
        status["last_crawl_age_seconds"] is None or status["last_crawl_age_seconds"] < 1800
    )
    if not nodes:
        status["notes"].append("Crawler file has no nodes.")
    elif not status["healthy"]:
        status["notes"].append("Crawler data exists but looks stale.")
    return status


def logos_node_setup_skill(base_url: str) -> str:
    base = base_url.rstrip("/")
    peer_flags = (" \\" + "\n    ").join(f"-p {peer['multiaddr']}" for peer in BOOTSTRAP_PEERS)
    linux_node = RELEASE_ASSETS["node"]["linux-x86_64"]
    linux_circuits = RELEASE_ASSETS["circuits"]["linux-x86_64"]
    pi_node = RELEASE_ASSETS["node"]["linux-aarch64"]
    pi_circuits = RELEASE_ASSETS["circuits"]["linux-aarch64"]
    mac_node = RELEASE_ASSETS["node"]["macos-aarch64"]
    mac_circuits = RELEASE_ASSETS["circuits"]["macos-aarch64"]
    return f"""# Logos Node Setup Agent Skill

Use this skill to help a user install, run, and verify a Logos Blockchain node on testnet.

## Safety and scope

- Installing software, running downloaded binaries, changing firewall settings, or requesting faucet funds can require explicit user confirmation in many agent environments.
- Prefer read-only checks first. Explain each command before executing it when it changes the machine.
- The node's wallet is an internal staking key store, not a general-purpose wallet.

## Release

- Node version: `{LOGOS_NODE_VERSION}`
- Circuits version: `{LOGOS_CIRCUITS_VERSION}`
- Release page: {LOGOS_RELEASE_URL}

Verified assets:

- Linux x86_64 node: {linux_node}
- Linux x86_64 circuits: {linux_circuits}
- Linux aarch64 node: {pi_node}
- Linux aarch64 circuits: {pi_circuits}
- macOS aarch64 node: {mac_node}
- macOS aarch64 circuits: {mac_circuits}

## Install

Detect the platform with `uname -s` and `uname -m`, then download the matching node and circuits archives:

```sh
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    NODE_URL="{linux_node}"
    CIRCUITS_URL="{linux_circuits}"
    ;;
  Linux-aarch64|Linux-arm64)
    NODE_URL="{pi_node}"
    CIRCUITS_URL="{pi_circuits}"
    ;;
  Darwin-arm64)
    NODE_URL="{mac_node}"
    CIRCUITS_URL="{mac_circuits}"
    ;;
  *)
    echo "Unsupported platform: $(uname -s)-$(uname -m)" >&2
    exit 1
    ;;
esac

curl -L -O "$CIRCUITS_URL"
curl -L -O "$NODE_URL"
tar -xf logos-blockchain-circuits-*.tar.gz
tar -xf logos-blockchain-node-*.tar.gz
rm -rf ~/.logos-blockchain-circuits
mv logos-blockchain-circuits-*/ ~/.logos-blockchain-circuits
chmod +x ./logos-blockchain-node
```

On macOS, if a circuit witness binary is blocked by Gatekeeper, inspect the real circuit directory first, then clear quarantine on the circuit tree:

```sh
xattr -lr ~/.logos-blockchain-circuits | grep quarantine || true
xattr -dr com.apple.quarantine ~/.logos-blockchain-circuits
```

## Initialize

Run `init` with all current bootstrap peers:

```sh
./logos-blockchain-node init \\
    {peer_flags}
```

The default node API port is `8080`. Change `api_port` in `user_config.yaml` only when another local service already uses it.

## Run

```sh
./logos-blockchain-node user_config.yaml
```

Keep this process running while checking status from another terminal.

## Verify locally

```sh
curl -s http://localhost:8080/cryptarchia/info | jq .
curl -s http://localhost:8080/network/info | jq .
```

Expected signals:

- `/cryptarchia/info` returns a mode, slot, height, tip, and lib.
- `mode` starts as `Bootstrapping` and eventually becomes `Online`.
- `/network/info` returns a `peer_id`, `n_peers > 0`, and `n_connections > 0`.
- `/network/peers` is not required. Observed node builds return `404` for that route.

## Verify on Logos Live

Extract the peer id:

```sh
curl -s http://localhost:8080/network/info | jq -r .peer_id
```

Then ask Logos Live whether that peer is visible:

```text
{base}/api/agent/verify-node/{{peer_id}}
{base}/api/agent/node-visibility/{{peer_id}}
```

The node appears on the map after the libp2p crawler observes its public peer record and geolocates the IP. If it does not appear yet, wait for the crawler, confirm UDP connectivity, and optionally publish a `#geo lat,lon` plus `#live` zone message.

## Optional faucet and consensus participation

Map visibility does not require faucet funds. Consensus participation does require stake. Wait until `/cryptarchia/info` reports `Online`, then find one public key from the node config:

```sh
grep -A3 known_keys user_config.yaml
```

Open the testnet faucet and request funds for that public key:

```text
https://devnet.blockchain.logos.co/web/faucet/
```

Submitting a faucet request is an external web action. If an agent is operating for a user, ask for confirmation before submitting the form. After 1-2 minutes, check the balance:

```sh
curl -s http://localhost:8080/wallet/<public-key>/balance | jq .
```

The faucet UTXO needs to age before the node can participate in the consensus lottery. On the current devnet this is approximately 3.5 hours.

## Useful machine-readable endpoints

- Manifest: {base}/api/agent/manifest
- Bootstrap peers: {base}/api/agent/bootstrap-peers
- Network state: {base}/api/agent/state
- Telemetry: {base}/api/telemetry
- Crawler status: {base}/api/agent/crawler/status
"""


def _hour_bucket(ts: int | float) -> int:
    return int(ts // 3600 * 3600)


def _parse_log_ts(line: str) -> int | None:
    match = _ISO_TS.search(line)
    if not match:
        return None
    raw = match.group(1).replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.astimezone(timezone.utc).timestamp())
    except ValueError:
        return None


def parse_peer_log_observations(text: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for line in _ANSI.sub("", text).splitlines():
        ts = _parse_log_ts(line)
        if ts is None:
            continue
        for peer_match in _PEER_ID.finditer(line):
            observations.append({"peer_id": peer_match.group(1), "ts": ts})
    return observations


def parse_stake_log_points(text: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for line in _ANSI.sub("", text).splitlines():
        ts = _parse_log_ts(line)
        if ts is None:
            continue
        match = _STAKE_VALUE.search(line)
        if not match:
            continue
        value = float(match.group(1).replace(",", "").replace("_", ""))
        points.append({"ts": ts, "value": value, "source": "log"})
    return points


def _read_log_telemetry(log_dir: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    log_path = Path(log_dir).expanduser()
    if not log_path.exists():
        return [], [], []
    files = sorted(log_path.glob("logos-blockchain.*"))[-40:]
    observations: list[dict[str, Any]] = []
    stake_points: list[dict[str, Any]] = []
    used_files: list[str] = []
    for path in files:
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        used_files.append(str(path))
        observations.extend(parse_peer_log_observations(text))
        stake_points.extend(parse_stake_log_points(text))
    return observations, stake_points, used_files


def _load_external_telemetry() -> dict[str, Any] | None:
    try:
        path = Path(TELEMETRY_FILE).expanduser()
        if path.exists():
            return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _observations_from_nodes(nodes: list[dict[str, Any]], now: int, window_hours: int) -> list[dict[str, Any]]:
    start = _hour_bucket(now - window_hours * 3600)
    observations: list[dict[str, Any]] = []
    for node in nodes:
        peer_id = node.get("peer_id")
        if not peer_id:
            continue
        first_seen = int(node.get("first_seen") or node.get("last_seen") or now)
        last_seen = int(node.get("last_seen") or now)
        if node.get("self") and not node.get("last_seen"):
            first_seen = now
            last_seen = now
        first_hour = max(_hour_bucket(first_seen), start)
        last_hour = min(_hour_bucket(last_seen), _hour_bucket(now))
        for bucket in range(first_hour, last_hour + 1, 3600):
            observations.append({"peer_id": peer_id, "ts": bucket})
    return observations


def _normalize_stake_points(points: list[dict[str, Any]], window_start: int, window_end: int) -> list[dict[str, Any]]:
    normalized = sorted(
        (
            {
                "ts": _hour_bucket(float(point["ts"])),
                "value": float(point["value"]),
                "source": point.get("source", "telemetry"),
            }
            for point in points
            if point.get("ts") is not None and point.get("value") is not None
        ),
        key=lambda p: p["ts"],
    )
    if not normalized:
        return []

    result: list[dict[str, Any]] = []
    current = normalized[0]["value"]
    idx = 0
    for bucket in range(window_start, window_end + 1, 3600):
        while idx < len(normalized) and normalized[idx]["ts"] <= bucket:
            current = normalized[idx]["value"]
            idx += 1
        result.append({"ts": bucket, "value": current})
    return result


def build_telemetry_snapshot(
    nodes: list[dict[str, Any]],
    observations: list[dict[str, Any]] | None = None,
    stake_points: list[dict[str, Any]] | None = None,
    now: int | None = None,
    window_hours: int = 24 * 20,
    source: str = "derived",
) -> dict[str, Any]:
    now = int(now or time.time())
    end = _hour_bucket(now)
    start = _hour_bucket(now - window_hours * 3600)
    observations = list(observations or [])
    if not observations:
        observations = _observations_from_nodes(nodes, now, window_hours)
        source = "crawler-first-last-seen"
    warnings: list[str] = []
    if source == "crawler-first-last-seen":
        warnings.append(
            "Uptime is synthesized from crawler first_seen/last_seen ranges; treat percentages as visibility estimates, not continuous observed availability."
        )

    buckets: dict[int, set[str]] = {bucket: set() for bucket in range(start, end + 1, 3600)}
    peer_hours: dict[str, set[int]] = {}
    for obs in observations:
        peer_id = obs.get("peer_id")
        ts = obs.get("ts")
        if not peer_id or ts is None:
            continue
        bucket = _hour_bucket(float(ts))
        if bucket < start or bucket > end:
            continue
        buckets.setdefault(bucket, set()).add(peer_id)
        peer_hours.setdefault(peer_id, set()).add(bucket)

    active_hourly = [
        {"ts": bucket, "count": len(peers)}
        for bucket, peers in sorted(buckets.items())
    ]
    peer_uptime = sorted(
        (
            {
                "peer_id": peer_id,
                "active_hours": len(hours),
                "uptime_pct": _pct(len(hours), len(active_hourly)),
                "hours": sorted(hours),
            }
            for peer_id, hours in peer_hours.items()
        ),
        key=lambda item: (-item["active_hours"], item["peer_id"]),
    )[:160]

    stake_series = _normalize_stake_points(stake_points or [], start, end)
    annotations: list[dict[str, Any]] = []
    for prev, cur in zip(stake_series, stake_series[1:]):
        if prev["value"] > 0 and cur["value"] >= prev["value"] * 2:
            annotations.append({
                "ts": cur["ts"],
                "kind": "stake-spike",
                "label": f"stake spike to {cur['value']:,.0f}",
            })

    latest_stake = stake_series[-1]["value"] if stake_series else None
    return {
        "source": source,
        "window_hours": window_hours,
        "active_peers_hourly": active_hourly,
        "peer_uptime": peer_uptime,
        "stake_estimate_hourly": stake_series,
        "annotations": annotations,
        "warnings": warnings,
        "summary": {
            "active_latest": active_hourly[-1]["count"] if active_hourly else 0,
            "active_peak": max((p["count"] for p in active_hourly), default=0),
            "tracked_peers": len(peer_hours),
            "latest_total_stake": latest_stake,
            "stake_points": len(stake_series),
        },
    }


def _external_telemetry_snapshot(nodes: list[dict[str, Any]], now: int) -> dict[str, Any] | None:
    external = _load_external_telemetry()
    if not external:
        return None
    if any(key in external for key in ("active_peers_hourly", "peer_uptime", "stake_estimate_hourly")):
        external.setdefault("source", "telemetry-file")
        external.setdefault("summary", {})
        external["summary"].setdefault(
            "active_latest",
            (external.get("active_peers_hourly") or [{"count": 0}])[-1].get("count", 0),
        )
        external["summary"].setdefault(
            "tracked_peers",
            len(external.get("peer_uptime") or []),
        )
        external["summary"].setdefault(
            "latest_total_stake",
            (external.get("stake_estimate_hourly") or [{"value": None}])[-1].get("value"),
        )
        external.setdefault("annotations", [])
        external.setdefault("warnings", [])
        return external
    return build_telemetry_snapshot(
        nodes=nodes,
        observations=external.get("observations") or [],
        stake_points=external.get("stake_points") or [],
        now=now,
        source="telemetry-file",
    )


def build_runtime_telemetry(
    nodes: list[dict[str, Any]],
    stake: dict[str, Any] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    now = int(now or time.time())
    external = _external_telemetry_snapshot(nodes, now)
    if external:
        return external

    observations, stake_points, log_files = _read_log_telemetry(LOG_DIR)
    window_hours = 24 * 20
    source = "logs" if observations or stake_points else "crawler-first-last-seen"
    if not observations:
        seen_values = [
            int(value)
            for node in nodes
            for value in (node.get("first_seen"), node.get("last_seen"))
            if value
        ]
        if seen_values:
            window_hours = min(window_hours, max(24, int((now - min(seen_values)) // 3600) + 2))
    if not stake_points and stake:
        stake_value = stake.get("total_active_stake") or stake.get("stake_estimate")
        if stake_value:
            stake_points.append({"ts": now, "value": stake_value, "source": "stake-summary"})
    telemetry = build_telemetry_snapshot(
        nodes=nodes,
        observations=observations,
        stake_points=stake_points,
        now=now,
        window_hours=window_hours,
        source=source,
    )
    telemetry["log_files"] = log_files[-5:]
    return telemetry


def _top_counts(counter: Counter, key_name: str, limit: int = 10) -> list[dict[str, Any]]:
    total = sum(counter.values())
    return [
        {key_name: key, "count": count, "pct": _pct(count, total)}
        for key, count in counter.most_common(limit)
    ]


def build_network_summary(
    nodes: list[dict[str, Any]],
    network: dict[str, Any],
    chain: dict[str, Any],
) -> dict[str, Any]:
    total = len(nodes)
    online = sum(1 for n in nodes if n.get("self") or n.get("online") is not False)
    country_counts = Counter(n.get("country") or "Unknown" for n in nodes)
    asn_counts = Counter(n.get("asn") or n.get("org") or n.get("isp") or "Unknown" for n in nodes)
    city_counts = Counter(
        ", ".join(part for part in (n.get("city"), n.get("country")) if part) or "Unknown"
        for n in nodes
    )
    infra_counts = Counter(classify_node_environment(n) for n in nodes)

    top_asn = asn_counts.most_common(1)
    top_country = country_counts.most_common(1)

    return {
        "health": {
            "mode": chain.get("mode") or "Unknown",
            "reported_peers": network.get("n_peers", 0),
            "connections": network.get("n_connections", 0),
            "pending_connections": network.get("n_pending_connections", 0),
        },
        "nodes": {
            "total": total,
            "online": online,
            "offline": max(total - online, 0),
            "countries": len(country_counts),
            "asns": len(asn_counts),
            "online_pct": _pct(online, total),
        },
        "geography": {
            "top_countries": _top_counts(country_counts, "country"),
            "top_cities": _top_counts(city_counts, "city"),
            "top_country_share_pct": _pct(top_country[0][1], total) if top_country else 0.0,
        },
        "infrastructure": {
            "mix": dict(infra_counts),
            "top_asns": _top_counts(asn_counts, "asn"),
            "top_asn_share_pct": _pct(top_asn[0][1], total) if top_asn else 0.0,
        },
    }


def _node_key(node: dict[str, Any]) -> str:
    return str(node.get("peer_id") or node.get("ip") or "")


def merge_peer_snapshot(
    local_data: dict[str, Any],
    fallback_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use published crawler peers when the local node exposes no peer rows."""
    local_nodes = local_data.get("nodes") or []
    reported_peers = int((local_data.get("network") or {}).get("n_peers") or 0)
    has_discovered_peers = any(not n.get("self") for n in local_nodes)
    local_node_available = bool((local_data.get("network") or {}).get("peer_id") or local_data.get("chain"))

    if has_discovered_peers or not fallback_data:
        local_data["peer_data_source"] = "local"
        return local_data

    if local_node_available and reported_peers <= len(local_nodes):
        local_data["peer_data_source"] = "local"
        return local_data

    merged_nodes: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_ips: set[str] = set()

    for node in local_nodes:
        merged_nodes.append(node)
        if _node_key(node):
            seen_keys.add(_node_key(node))
        if node.get("ip"):
            seen_ips.add(node["ip"])

    for node in fallback_data.get("nodes") or []:
        if node.get("self"):
            continue
        key = _node_key(node)
        ip = node.get("ip")
        if (key and key in seen_keys) or (ip and ip in seen_ips):
            continue
        copy = dict(node)
        copy["source"] = "published-fallback"
        merged_nodes.append(copy)
        if key:
            seen_keys.add(key)
        if ip:
            seen_ips.add(ip)

    local_data["nodes"] = merged_nodes
    local_data["peer_data_source"] = "published-fallback"
    local_data["peer_snapshot"] = {
        "updated": fallback_data.get("updated"),
        "nodes": len(fallback_data.get("nodes") or []),
    }
    return local_data


def hydrate_content_snapshot(
    local_data: dict[str, Any],
    fallback_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Restore static/public content feeds when the live backend has none."""
    if not fallback_data:
        local_data["content_data_source"] = "local"
        return local_data

    used_fallback = False
    for key in ("events", "youtube", "zone_messages", "github", "discourse", "stake"):
        current = local_data.get(key)
        if current:
            continue
        fallback = fallback_data.get(key)
        if fallback:
            local_data[key] = fallback
            used_fallback = True

    local_data["content_data_source"] = "published-fallback" if used_fallback else "local"
    return local_data


def summarize_recent_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    slots = [
        b.get("header", {}).get("slot")
        for b in blocks
        if b.get("header", {}).get("slot") is not None
    ]
    leader_counts: Counter[str] = Counter()
    tx_count = 0
    op_count = 0
    empty_blocks = 0
    recent: list[dict[str, Any]] = []

    for block in blocks:
        header = block.get("header") or {}
        leader = (header.get("proof_of_leadership") or {}).get("leader_key") or "unknown"
        leader_counts[leader] += 1
        txs = block.get("transactions") or []
        if not txs:
            empty_blocks += 1
        tx_count += len(txs)
        block_ops = 0
        opcodes: Counter[int] = Counter()
        for tx in txs:
            for op in ((tx.get("mantle_tx") or {}).get("ops") or []):
                block_ops += 1
                op_count += 1
                opcodes[op.get("opcode")] += 1
        recent.append({
            "slot": header.get("slot"),
            "block_root": header.get("block_root") or "",
            "leader_key": leader,
            "transactions": len(txs),
            "operations": block_ops,
            "opcodes": dict(opcodes),
        })

    slot_min = min(slots) if slots else None
    slot_max = max(slots) if slots else None
    slot_span = (slot_max - slot_min + 1) if slot_min is not None and slot_max is not None else 0
    missed = max(slot_span - len(set(slots)), 0)
    top_leader = leader_counts.most_common(1)

    return {
        "window": {
            "blocks": len(blocks),
            "slot_min": slot_min,
            "slot_max": slot_max,
            "slot_span": slot_span,
            "missed_or_unseen_slots": missed,
            "empty_blocks": empty_blocks,
            "transactions": tx_count,
            "operations": op_count,
        },
        "leader_diversity": {
            "unique_leaders": len(leader_counts),
            "top_leader_share_pct": _pct(top_leader[0][1], len(blocks)) if top_leader else 0.0,
        },
        "top_leaders": [
            {
                "leader_key": leader,
                "blocks": count,
                "pct": _pct(count, len(blocks)),
            }
            for leader, count in leader_counts.most_common(10)
        ],
        "recent": sorted(recent, key=lambda item: item.get("slot") or 0, reverse=True)[:20],
    }


def _load_crawler_peers() -> tuple[dict[str, str], dict[str, dict]] | tuple[None, None]:
    """Load peers from crawler node_db.json.
    Returns (peer_id→ip, peer_id→meta) or (None, None) on failure."""
    try:
        data = json.loads(Path(PEERS_FILE).read_text())
        last_crawl = data.get("last_crawl", 0)
        ip_map: dict[str, str] = {}
        meta_map: dict[str, dict] = {}
        for node in data.get("nodes", {}).values():
            pid = node["peer_id"]
            for addr in node.get("addrs", []):
                m = re.search(r"/ip4/(\d+\.\d+\.\d+\.\d+)/", addr)
                if m:
                    ip_map[pid] = m.group(1)
                    break
            if pid in ip_map:
                meta_map[pid] = {
                    "first_seen": node.get("first_seen", 0),
                    "last_seen":  node.get("last_seen", 0),
                    "online":     node.get("last_seen", 0) == last_crawl,
                }
        return (ip_map, meta_map) if ip_map else (None, None)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return (None, None)


def _parse_peers(log_dir: str) -> dict[str, str]:
    """Return {peer_id: ip} from the two most-recent log files."""
    log_path = Path(log_dir).expanduser()
    if not log_path.exists():
        return {}
    files = sorted(log_path.glob("logos-blockchain.*"))
    peers: dict[str, str] = {}
    for f in files[-2:]:
        try:
            text = _ANSI.sub("", f.read_text(errors="ignore"))
            for m in _PEER_ADDR.finditer(text):
                ip, peer_id = m.group(1), m.group(2)
                if _is_public(ip):
                    peers[peer_id] = ip   # last-seen address wins
        except OSError:
            pass
    return peers


async def _geolocate(ips: list[str]) -> dict[str, dict]:
    """Batch-geolocate via ip-api.com (free, no key, 100/req, 15 req/min)."""
    result: dict[str, dict] = {}
    if not ips:
        return result
    fields = "query,country,countryCode,city,lat,lon,isp,org,as"
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(0, len(ips), 100):
            batch = ips[i : i + 100]
            try:
                # ip-api's free batch endpoint is HTTP-only; HTTPS requires their paid tier.
                resp = await client.post(
                    "http://ip-api.com/batch",
                    json=[{"query": ip, "fields": fields} for ip in batch],
                )
                if resp.status_code == 200:
                    for row in resp.json():
                        result[row["query"]] = row
            except Exception:
                pass
    return result


async def _get_public_ip(client: httpx.AsyncClient) -> str | None:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = await client.get(url, timeout=5)
            if r.status_code == 200:
                return r.text.strip()
        except Exception:
            pass
    return None


def _load_published_snapshot_from_file() -> dict[str, Any] | None:
    candidates = [
        os.getenv("PUBLISHED_NETWORK_FILE", ""),
        str(Path(__file__).parent / "pages" / "network.json"),
        str(Path(__file__).parent / "network.json"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser()
            if path.exists():
                return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _load_published_snapshot_from_git() -> dict[str, Any] | None:
    if os.getenv("ENABLE_GIT_SNAPSHOT_FALLBACK") != "1":
        return None
    try:
        result = subprocess.run(
            ["git", "show", "origin/gh-pages:network.json"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return None


async def _load_published_snapshot(client: httpx.AsyncClient) -> dict[str, Any] | None:
    snapshot = _load_published_snapshot_from_file() or _load_published_snapshot_from_git()
    if snapshot:
        return snapshot
    if not PUBLISHED_NETWORK_URL:
        return None
    try:
        resp = await client.get(PUBLISHED_NETWORK_URL, timeout=6)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return None
    return None


async def _fetch_recent_blocks(client: httpx.AsyncClient, limit: int = 30) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    try:
        resp = await client.get(f"{NODE_URL}/cryptarchia/headers?limit={limit}", timeout=5)
        if resp.status_code != 200:
            return summarize_recent_blocks([])
        hashes = resp.json()
    except Exception:
        return summarize_recent_blocks([])

    for block_hash in hashes[:limit]:
        try:
            block_resp = await client.post(
                f"{NODE_URL}/storage/block",
                content=json.dumps(block_hash),
                headers={"Content-Type": "application/json"},
                timeout=4,
            )
            if block_resp.status_code == 200:
                blocks.append(block_resp.json())
        except Exception:
            continue

    return summarize_recent_blocks(blocks)


async def _build_data() -> dict:
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            chain = (await client.get(f"{NODE_URL}/cryptarchia/info")).json()
        except Exception:
            chain = {}
        try:
            net = (await client.get(f"{NODE_URL}/network/info")).json()
        except Exception:
            net = {}
        recent_blocks = await _fetch_recent_blocks(client)
        own_ip = await _get_public_ip(client)
        published_snapshot = await _load_published_snapshot(client)

    crawler_ips, crawler_meta = _load_crawler_peers()
    peers_by_id = crawler_ips or _parse_peers(LOG_DIR)
    peer_meta   = crawler_meta or {}
    own_peer_id = net.get("peer_id", "")

    # Include own IP in geolocation batch
    unique_ips = list(set(peers_by_id.values()))
    if own_ip and own_ip not in unique_ips:
        unique_ips.append(own_ip)
    geo = await _geolocate(unique_ips)

    nodes = []

    # Add ourselves first
    if own_ip:
        g = geo.get(own_ip, {})
        lat, lon = g.get("lat"), g.get("lon")
        if lat is not None and lon is not None:
            nodes.append({
                "peer_id":      own_peer_id,
                "ip":           own_ip,
                "lat":          lat,
                "lon":          lon,
                "country":      g.get("country", ""),
                "country_code": g.get("countryCode", ""),
                "city":         g.get("city", ""),
                "isp":          g.get("isp", ""),
                "org":          g.get("org", ""),
                "asn":          g.get("as", ""),
                "self":         True,
            })

    for peer_id, ip in peers_by_id.items():
        g = geo.get(ip, {})
        lat, lon = g.get("lat"), g.get("lon")
        if lat is None or lon is None:
            continue
        meta = peer_meta.get(peer_id, {})
        nodes.append({
            "peer_id":      peer_id,
            "ip":           ip,
            "lat":          lat,
            "lon":          lon,
            "country":      g.get("country", ""),
            "country_code": g.get("countryCode", ""),
            "city":         g.get("city", ""),
            "isp":          g.get("isp", ""),
            "org":          g.get("org", ""),
            "asn":          g.get("as", ""),
            "self":         False,
            "online":       meta.get("online", True),
            "first_seen":   meta.get("first_seen", 0),
            "last_seen":    meta.get("last_seen", 0),
        })

    # Deduplicate by IP (same IP, multiple peer IDs → one marker)
    seen_ips: set[str] = set()
    deduped = []
    for n in nodes:
        if n["ip"] not in seen_ips:
            seen_ips.add(n["ip"])
            deduped.append(n)

    data = {
        "chain":    chain,
        "network":  net,
        "nodes":    deduped,
        "total_peers_in_logs": len(peers_by_id),
        "updated":  time.time(),
    }
    data = merge_peer_snapshot(data, published_snapshot)
    data = hydrate_content_snapshot(data, published_snapshot)
    data["nodes"] = [
        {**node, "environment": classify_node_environment(node)}
        for node in data.get("nodes") or []
    ]
    data["summary"] = build_network_summary(data["nodes"], net, chain)
    data["recent_blocks"] = recent_blocks
    data["telemetry"] = build_runtime_telemetry(data["nodes"], data.get("stake"), int(data["updated"]))
    data["agent"] = {
        "schema_url": "/api/agent/schema",
        "state_url": "/api/agent/state",
        "skill_url": "/agents/logos-network-skill.md",
        "well_known_url": "/.well-known/logos-live.json",
    }
    return data


# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="Logos Node Visualizer")


@app.get("/api/network")
async def api_network():
    global _cache, _cache_ts
    async with _cache_lock:
        if _cache is None or (time.time() - _cache_ts) > CACHE_TTL:
            _cache    = await _build_data()
            _cache_ts = time.time()
    return _cache


@app.get("/api/telemetry")
async def api_telemetry():
    data = await api_network()
    return data.get("telemetry", {})


@app.get("/api/agent/manifest")
async def api_agent_manifest(request: Request):
    return build_agent_manifest(_base_url(request))


@app.get("/api/agent/bootstrap-peers")
async def api_bootstrap_peers():
    return {
        "network": "Logos testnet",
        "release": LOGOS_NODE_VERSION,
        "bootstrap_peers": build_bootstrap_peers(),
    }


@app.get("/api/agent/crawler/status")
async def api_crawler_status():
    return build_crawler_status()


@app.get("/api/agent/node-visibility/{peer_id}")
async def api_node_visibility(peer_id: str):
    validate_peer_id(peer_id)
    data = await api_network()
    return build_node_visibility(peer_id, data)


@app.get("/api/agent/verify-node/{peer_id}")
async def api_verify_node(peer_id: str):
    validate_peer_id(peer_id)
    data = await api_network()
    return build_node_verification(peer_id, data)


@app.get("/api/agent/state")
async def api_agent_state():
    data = await api_network()
    return {
        "updated": data.get("updated"),
        "chain": data.get("chain"),
        "network": data.get("network"),
        "summary": data.get("summary"),
        "recent_blocks": data.get("recent_blocks"),
        "telemetry": data.get("telemetry"),
        "peer_data_source": data.get("peer_data_source"),
        "peer_snapshot": data.get("peer_snapshot"),
        "nodes": [
            {
                "peer_id": n.get("peer_id"),
                "ip": n.get("ip"),
                "country": n.get("country"),
                "city": n.get("city"),
                "asn": n.get("asn"),
                "isp": n.get("isp"),
                "org": n.get("org"),
                "environment": n.get("environment"),
                "online": n.get("online", True),
                "self": n.get("self", False),
                "first_seen": n.get("first_seen"),
                "last_seen": n.get("last_seen"),
            }
            for n in data.get("nodes") or []
        ],
    }


@app.get("/api/agent/schema")
async def api_agent_schema(request: Request):
    base = _base_url(request)
    return {
        "name": "Logos Network Intelligence",
        "description": "Machine-readable entrypoint for inspecting the current Logos network state.",
        "manifest": _url(base, "/api/agent/manifest"),
        "endpoints": {
            "network": {
                "url": "/api/network",
                "description": "Full dashboard payload, including map nodes and UI feeds.",
            },
            "agent_state": {
                "url": "/api/agent/state",
                "description": "Compact agent-oriented state: chain, network, decentralization summary, nodes, and recent blocks.",
            },
            "skill": {
                "url": "/agents/logos-network-skill.md",
                "description": "Markdown instructions agents can use to inspect Logos or help set up a node.",
            },
            "telemetry": {
                "url": "/api/telemetry",
                "description": "Historical node activity, uptime, and stake telemetry when logs or telemetry.json are available.",
            },
            "bootstrap_peers": {
                "url": "/api/agent/bootstrap-peers",
                "description": "Current bootstrap multiaddrs for node init.",
            },
            "verify_node": {
                "url": "/api/agent/verify-node/{peer_id}",
                "description": "Checks local API health, connectivity, crawler observation, map visibility, and telemetry for a peer id.",
            },
            "node_visibility": {
                "url": "/api/agent/node-visibility/{peer_id}",
                "description": "Explains whether a peer id is visible on the map and what to do next.",
            },
            "setup_skill": {
                "url": "/agents/logos-node-setup-skill.md",
                "description": "Concrete setup and verification instructions for agents installing a Logos node.",
            },
        },
        "notes": [
            "Peer infrastructure classes are inferred from public ASN/ISP strings and should be treated as best-effort.",
            "When local peer discovery is empty, nodes may be merged from the published gh-pages snapshot.",
        ],
    }


@app.get("/.well-known/logos-live.json")
async def well_known_logos_live(request: Request):
    base = _base_url(request)
    return {
        "name": "Logos Live",
        "network": "Logos testnet",
        "skill_version": "1.0.0",
        "api": _url(base, "/api/agent/state"),
        "manifest": _url(base, "/api/agent/manifest"),
        "telemetry": _url(base, "/api/telemetry"),
        "schema": _url(base, "/api/agent/schema"),
        "skill": _url(base, "/agents/logos-network-skill.md"),
        "setup_skill": _url(base, "/agents/logos-node-setup-skill.md"),
        "capabilities": build_agent_manifest(base)["capabilities"],
    }


@app.get("/agents/logos-network-skill.md", response_class=PlainTextResponse)
async def logos_network_skill(request: Request):
    base = _base_url(request)
    return f"""# Logos Network Agent Skill

Use this page as the agent entrypoint for exploring the Logos network.

## Inspect the network

0. Fetch `{base}/api/agent/manifest` for the complete agent contract.
1. Fetch `/api/agent/schema` to discover available endpoints.
2. Fetch `/api/agent/state` for the current chain, peer, decentralization, and recent block state.
3. Fetch `/api/telemetry` for hourly active peers, peer uptime, and stake estimate history.
4. Treat `environment` as an inference from public ASN/ISP metadata, not a proof that a node is home-hosted or VPS-hosted.
5. If `peer_data_source` is `published-fallback`, local peer discovery did not expose peer rows and published crawler data was merged in as a temporary network map.

## Helpful questions this endpoint can answer

- Is the local node online and advancing?
- How many peers and connections does the node report?
- Which countries, ASNs, and hosting categories dominate discovered peers?
- How concentrated are recent block leaders?
- Which nodes look residential, hosting, or unknown from public IP metadata?
- How many peers were active per hour?
- Which peers have the best observed uptime?
- Did active stake change abruptly during redistribution or faucet events?

## Setup guidance for another agent

To help a user set up a Logos node, fetch the dedicated setup skill first:

`{base}/agents/logos-node-setup-skill.md`

Then verify:

- the node HTTP API responds at `/cryptarchia/info`
- `/network/info` reports a peer id and nonzero peers/connections
- the local dashboard at this server can read `/api/agent/state`
- `/api/telemetry` has activity from logs, crawler first/last seen, or a generated `telemetry.json`
- their public peer id passes `{base}/api/agent/verify-node/{{peer_id}}`

Do not assume `/network/peers` exists. Current observed nodes return `404` for that route; use libp2p crawling or published crawler snapshots for peer rows until a node API exposes them directly.
"""


@app.get("/agents/logos-node-setup-skill.md", response_class=PlainTextResponse)
async def logos_node_setup_skill_endpoint(request: Request):
    return logos_node_setup_skill(_base_url(request))


# Serve frontend last so /api/* routes take priority
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
