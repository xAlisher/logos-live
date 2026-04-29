"""
Logos Network Visualizer — backend
Reads peer IPs from node logs + Kademlia DHT entries, geolocates them,
serves data to the frontend map.
"""

import json
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# ── Config (override via env vars) ────────────────────────────────────────────
NODE_URL = os.getenv("NODE_URL", "http://127.0.0.1:8080")
LOG_DIR  = os.getenv(
    "LOG_DIR",
    os.path.expanduser("~/logos-blockchain-runbook/state/live-v0.1.2/logs"),
)
CACHE_TTL = int(os.getenv("CACHE_TTL", "30"))  # seconds

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
        own_ip = await _get_public_ip(client)

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

    return {
        "chain":    chain,
        "network":  net,
        "nodes":    deduped,
        "total_peers_in_logs": len(peers_by_id),
        "updated":  time.time(),
    }


# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(title="Logos Node Visualizer")


@app.get("/api/network")
async def api_network():
    global _cache, _cache_ts
    if _cache is None or (time.time() - _cache_ts) > CACHE_TTL:
        _cache    = await _build_data()
        _cache_ts = time.time()
    return _cache


# Serve frontend last so /api/* routes take priority
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
