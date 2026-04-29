"""
Logos Network Visualizer — backend
Reads peer IPs from node logs + Kademlia DHT entries, geolocates them,
serves data to the frontend map.
"""

import json
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
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

# ── Compiled patterns ──────────────────────────────────────────────────────────
_ANSI      = re.compile(r"\x1b\[[0-9;]*m")
_PEER_ADDR = re.compile(
    r"Added address /ip4/(\d+\.\d+\.\d+\.\d+)/\S+ to peer PeerId\(\"([^\"]+)\"\)"
)

# ── Cache ──────────────────────────────────────────────────────────────────────
_cache: dict | None = None
_cache_ts: float = 0.0


def _is_public(ip: str) -> bool:
    p = list(map(int, ip.split(".")))
    if p[0] == 10:                              return False
    if p[0] == 172 and 16 <= p[1] <= 31:       return False
    if p[0] == 192 and p[1] == 168:            return False
    if p[0] == 127:                             return False
    if p[0] == 100 and 64 <= p[1] <= 127:      return False  # CGNAT
    if p[0] >= 224:                             return False  # multicast/reserved
    return True


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

    if has_discovered_peers or not fallback_data or reported_peers <= len(local_nodes):
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
    if os.getenv("DISABLE_GIT_SNAPSHOT_FALLBACK") == "1":
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
    if _cache is None or (time.time() - _cache_ts) > CACHE_TTL:
        _cache    = await _build_data()
        _cache_ts = time.time()
    return _cache


@app.get("/api/agent/state")
async def api_agent_state():
    data = await api_network()
    return {
        "updated": data.get("updated"),
        "chain": data.get("chain"),
        "network": data.get("network"),
        "summary": data.get("summary"),
        "recent_blocks": data.get("recent_blocks"),
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
async def api_agent_schema():
    return {
        "name": "Logos Network Intelligence",
        "description": "Machine-readable entrypoint for inspecting the current Logos network state.",
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
        },
        "notes": [
            "Peer infrastructure classes are inferred from public ASN/ISP strings and should be treated as best-effort.",
            "When local peer discovery is empty, nodes may be merged from the published gh-pages snapshot.",
        ],
    }


@app.get("/.well-known/logos-live.json")
async def well_known_logos_live():
    return {
        "name": "Logos Live",
        "network": "Logos testnet",
        "api": "/api/agent/state",
        "schema": "/api/agent/schema",
        "skill": "/agents/logos-network-skill.md",
    }


@app.get("/agents/logos-network-skill.md", response_class=PlainTextResponse)
async def logos_network_skill():
    return """# Logos Network Agent Skill

Use this page as the agent entrypoint for exploring the Logos network.

## Inspect the network

1. Fetch `/api/agent/schema` to discover available endpoints.
2. Fetch `/api/agent/state` for the current chain, peer, decentralization, and recent block state.
3. Treat `environment` as an inference from public ASN/ISP metadata, not a proof that a node is home-hosted or VPS-hosted.
4. If `peer_data_source` is `published-fallback`, local peer discovery did not expose peer rows and published crawler data was merged in as a temporary network map.

## Helpful questions this endpoint can answer

- Is the local node online and advancing?
- How many peers and connections does the node report?
- Which countries, ASNs, and hosting categories dominate discovered peers?
- How concentrated are recent block leaders?
- Which nodes look residential, hosting, or unknown from public IP metadata?

## Setup guidance for another agent

To help a user set up a Logos node, use the official node setup guide first, then verify:

- the node HTTP API responds at `/cryptarchia/info`
- `/network/info` reports a peer id and nonzero peers/connections
- the local dashboard at this server can read `/api/agent/state`

Do not assume `/network/peers` exists. Current observed nodes return `404` for that route; use libp2p crawling or published crawler snapshots for peer rows until a node API exposes them directly.
"""


# Serve frontend last so /api/* routes take priority
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
