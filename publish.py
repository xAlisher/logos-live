#!/usr/bin/env python3
"""
Generate network.json from node_db.json + geo cache and push to GitHub Pages.
Run hourly via cron or: watch -n 3600 python3 publish.py
"""
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

BASE      = Path(__file__).parent
DB_FILE   = BASE / "peers.json"
GEO_CACHE = BASE / "geo_cache.json"
PAGES_DIR = BASE / "pages"
OUT_FILE  = PAGES_DIR / "network.json"
NODE_URL  = os.getenv("NODE_URL", "http://127.0.0.1:8080")


def load_db() -> dict:
    try:
        return json.loads(DB_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"nodes": {}, "last_crawl": 0}


def load_geo_cache() -> dict:
    try:
        return json.loads(GEO_CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_geo_cache(cache: dict):
    GEO_CACHE.write_text(json.dumps(cache, indent=2))


def extract_ip(addrs: list[str]) -> str | None:
    for addr in addrs:
        m = re.search(r"/ip4/(\d+\.\d+\.\d+\.\d+)/", addr)
        if m:
            return m.group(1)
    return None


async def geolocate_batch(ips: list[str]) -> dict:
    result = {}
    fields = "query,country,countryCode,city,lat,lon,isp,org,as"
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(0, len(ips), 100):
            batch = ips[i:i+100]
            try:
                resp = await client.post(
                    "http://ip-api.com/batch",
                    json=[{"query": ip, "fields": fields} for ip in batch],
                )
                if resp.status_code == 200:
                    for row in resp.json():
                        result[row["query"]] = row
            except Exception as e:
                print(f"  Geo error: {e}")
    return result


async def get_own_ip(client: httpx.AsyncClient) -> str | None:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            r = await client.get(url, timeout=5)
            if r.status_code == 200:
                return r.text.strip()
        except Exception:
            pass
    return None


async def get_chain_info(client: httpx.AsyncClient) -> tuple[dict, dict]:
    chain, net = {}, {}
    try:
        chain = (await client.get(f"{NODE_URL}/cryptarchia/info", timeout=3)).json()
    except Exception:
        pass
    try:
        net = (await client.get(f"{NODE_URL}/network/info", timeout=3)).json()
    except Exception:
        pass
    return chain, net


FAUCET_PK          = "fcadf75488f8048bd4db210e55b6da2c1960af0fda9c3ce73bb79b842c688a14"
STAKE_CACHE        = BASE / "stake_cache.json"
ZONE_BOARD_DIR  = Path(os.getenv(
    "ZONE_BOARD_DIR",
    os.path.expanduser("~/logos-blockchain-runbook/state/zone-board-v0.2.2"),
))
ZONE_SCAN_FILE  = BASE / "zone_scan.json"
GEO_HINTS_FILE  = BASE / "geo_hints.json"

LUMA_CALENDAR   = "cal-S3pdMJmDQDY9aT4"
YT_CHANNEL_ID   = "UCAI6Gk0R_1aGa76ShKFA78Q"
GH_ORG          = "logos-co"
DISCOURSE_URL   = "https://forum.logos.co/latest.json"
CACHE_FILE      = BASE / "feed_cache.json"


def load_feed_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_feed_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))

GH_TYPE_MAP = {
    "PushEvent":              ("Push",    "type-push"),
    "PullRequestEvent":       ("PR",      "type-pr"),
    "CreateEvent":            ("Create",  "type-create"),
    "ReleaseEvent":           ("Release", "type-release"),
    "IssueCommentEvent":      ("Comment", "type-create"),
    "PullRequestReviewEvent": ("Review",  "type-pr"),
}

DISCOURSE_CATEGORY = {4: "Circles", 6: "Circles", 8: "Announcements", 1: "General"}


async def fetch_latest_youtube(client: httpx.AsyncClient, cache: dict) -> dict:
    """Get latest Logos video via yt-dlp (if installed) or return cached."""
    # Try yt-dlp first — no consent issues
    try:
        import subprocess as sp
        result = sp.run(
            ["yt-dlp", "--quiet", "--no-warnings", "-j",
             "--playlist-items", "1", "--skip-download",
             "https://www.youtube.com/@LogosNetwork/videos"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            info = json.loads(result.stdout.strip().split("\n")[0])
            data = {"video_id": info["id"], "title": info["title"],
                    "published": info.get("upload_date", "")[:10]}
            print(f"  YouTube (yt-dlp): {data['title'][:60]}")
            cache["youtube"] = data
            return data
    except (FileNotFoundError, Exception) as e:
        print(f"  YouTube yt-dlp exception: {type(e).__name__}: {e}")
    # Fall back to cached
    if "youtube" in cache:
        print("  YouTube: using cached data")
        return cache["youtube"]
    print("  YouTube: no data available")
    return {}


async def _fetch_latest_youtube_unused(client: httpx.AsyncClient) -> dict:
    """Scrape Logos YouTube channel page to get latest video id + title."""
    try:
        resp = await client.get(
            "https://www.youtube.com/@LogosNetwork/videos",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                     "Accept-Language": "en-US,en;q=0.9",
                     "Cookie": "CONSENT=YES+cb; YSC=x; VISITOR_INFO1_LIVE=x"},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            print(f"  YouTube error: HTTP {resp.status_code}")
            return {}
        html = resp.text
        # Extract first videoId from ytInitialData
        m_id = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
        if not m_id:
            print("  YouTube: no videoId found")
            return {}
        video_id = m_id.group(1)
        # Extract matching title (look near the videoId)
        pos = m_id.start()
        snippet = html[max(0, pos-200):pos+500]
        m_title = re.search(r'"text":"([^"]{5,120})"', snippet)
        title = m_title.group(1) if m_title else "Latest from Logos"
        print(f"  YouTube: {title[:60]} ({video_id})")
        return {"video_id": video_id, "title": title}
    except Exception as e:
        print(f"  YouTube error: {e}")
        return {}


async def fetch_github(client: httpx.AsyncClient, cache: dict) -> list[dict]:
    """Fetch recent GitHub org events and return a clean feed list."""
    items = []
    seen = set()
    try:
        token = os.getenv("GITHUB_TOKEN", "")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "logos-live-monitor/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = await client.get(
            f"https://api.github.com/orgs/{GH_ORG}/events?per_page=100",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"  GitHub error: {resp.status_code} — using cache")
            return cache.get("github", [])

        for e in resp.json():
            mp = GH_TYPE_MAP.get(e.get("type", ""))
            if not mp:
                continue
            label, cls = mp
            repo = (e.get("repo") or {}).get("name", "").replace(f"{GH_ORG}/", "")
            p = e.get("payload") or {}
            title = ""
            url = f"https://github.com/{e.get('repo', {}).get('name', GH_ORG)}"
            if e["type"] == "PushEvent":
                commits = p.get("commits") or []
                if commits:
                    title = commits[0]["message"].split("\n")[0]
                    url = f"https://github.com/{e['repo']['name']}/commit/{commits[0]['sha']}"
            elif e["type"] == "PullRequestEvent":
                pr = p.get("pull_request") or {}
                title = pr.get("title", "")
                url = pr.get("html_url", url)
            elif e["type"] == "CreateEvent":
                title = f'new {p.get("ref_type","")} "{p.get("ref","")}"'
            elif e["type"] == "ReleaseEvent":
                rel = p.get("release") or {}
                title = rel.get("name") or rel.get("tag_name", "")
                url = rel.get("html_url", url)
            elif e["type"] == "IssueCommentEvent":
                title = (p.get("issue") or {}).get("title", "")
                url = (p.get("comment") or {}).get("html_url", url)
            elif e["type"] == "PullRequestReviewEvent":
                pr = p.get("pull_request") or {}
                title = pr.get("title", "")
                url = pr.get("html_url", url)
            key = f"{e['type']}:{repo}:{title}"
            if not title or key in seen:
                continue
            seen.add(key)
            items.append({
                "repo":      repo,
                "type":      label,
                "cls":       cls,
                "title":     title,
                "url":       url,
                "timestamp": e.get("created_at", ""),
            })
            if len(items) >= 40:
                break
    except Exception as ex:
        print(f"  GitHub error: {ex}")
        return cache.get("github", [])
    if items:
        cache["github"] = items
    print(f"  GitHub: {len(items)} events")
    return items


async def fetch_discourse(client: httpx.AsyncClient) -> list[dict]:
    """Fetch latest forum topics from Logos Discourse."""
    items = []
    try:
        resp = await client.get(f"{DISCOURSE_URL}?limit=40", timeout=10)
        if resp.status_code != 200:
            print(f"  Discourse error: {resp.status_code}")
            return []
        data = resp.json()
        for t in (data.get("topic_list") or {}).get("topics") or []:
            if t.get("pinned"):
                continue
            items.append({
                "title":     t["title"],
                "category":  DISCOURSE_CATEGORY.get(t.get("category_id"), "Forum"),
                "url":       f"https://forum.logos.co/t/{t['slug']}/{t['id']}",
                "timestamp": t.get("created_at", ""),
                "replies":   t.get("posts_count", 1) - 1,
            })
            if len(items) >= 30:
                break
    except Exception as ex:
        print(f"  Discourse error: {ex}")
    print(f"  Discourse: {len(items)} topics")
    return items


def load_stake_cache() -> dict:
    try:
        return json.loads(STAKE_CACHE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"recipients": [], "total_distributed": 0, "leader_blocks": {}}


def save_stake_cache(cache: dict):
    STAKE_CACHE.write_text(json.dumps(cache, indent=2))


async def fetch_stake(client: httpx.AsyncClient) -> dict:
    """Scan recent blocks for faucet distribution + block leader counts."""
    cache = load_stake_cache()
    known_recipients: set[str] = set(cache.get("recipients", []))
    cumulative_distributed: int = cache.get("total_distributed", 0)
    leader_blocks: dict[str, int] = cache.get("leader_blocks", {})

    try:
        resp = await client.get(f"{NODE_URL}/cryptarchia/headers?limit=100", timeout=5)
        if resp.status_code != 200:
            return _stake_summary(cache)
        hashes = resp.json()
    except Exception:
        return _stake_summary(cache)

    new_recipients = 0
    new_distributed = 0
    faucet_remainder: int | None = None

    for h in hashes:
        try:
            r = await client.post(
                f"{NODE_URL}/storage/block",
                content=json.dumps(h),
                headers={"Content-Type": "application/json"},
                timeout=3,
            )
            if r.status_code != 200:
                continue
            block = r.json()
        except Exception:
            continue

        # Track block leader
        lk = block.get("header", {}).get("proof_of_leadership", {}).get("leader_key", "")
        if lk:
            leader_blocks[lk] = leader_blocks.get(lk, 0) + 1

        for tx in block.get("transactions", []):
            for op in tx.get("mantle_tx", {}).get("ops", []):
                if op.get("opcode") != 0:
                    continue
                outputs = op.get("payload", {}).get("outputs", [])
                if len(outputs) != 2:
                    continue
                small, large = sorted(outputs, key=lambda x: x["value"])
                # Faucet tx pattern: small amount to recipient + huge remainder to faucet
                if large.get("pk") == FAUCET_PK and small["value"] < 1_000_000_000:
                    recipient_pk = small["pk"]
                    amount = small["value"]
                    if recipient_pk not in known_recipients:
                        known_recipients.add(recipient_pk)
                        new_distributed += amount
                        new_recipients += 1
                    # Track lowest remainder seen = most up-to-date faucet balance
                    rem = large["value"]
                    if faucet_remainder is None or rem < faucet_remainder:
                        faucet_remainder = rem

    cumulative_distributed += new_distributed

    cache["recipients"] = list(known_recipients)
    cache["total_distributed"] = cumulative_distributed
    cache["leader_blocks"] = leader_blocks
    if faucet_remainder is not None:
        cache["faucet_remainder"] = faucet_remainder
    save_stake_cache(cache)

    summary = _stake_summary(cache)
    print(f"  Stake: {summary['recipients']} recipients, "
          f"{summary['total_distributed']:,} distributed, "
          f"{summary['unique_leaders']} leaders")
    return summary


def _stake_summary(cache: dict) -> dict:
    leader_blocks: dict[str, int] = cache.get("leader_blocks", {})
    top_leaders = sorted(leader_blocks.items(), key=lambda x: -x[1])[:10]
    total_leader_blocks = sum(leader_blocks.values())
    return {
        "recipients":        len(cache.get("recipients", [])),
        "total_distributed": cache.get("total_distributed", 0),
        "faucet_remainder":  cache.get("faucet_remainder"),
        "unique_leaders":    len(leader_blocks),
        "total_leader_blocks": total_leader_blocks,
        "top_leaders": [
            {"pk": pk[:16], "blocks": n,
             "pct": round(100 * n / total_leader_blocks, 1) if total_leader_blocks else 0}
            for pk, n in top_leaders
        ],
    }


def _channel_hex_to_name(hex_id: str) -> str:
    """Decode a zone-board channel hex ID to a human-readable name."""
    try:
        raw = bytes.fromhex(hex_id).rstrip(b"\x00")
        name = raw.decode("utf-8")
        # "logos:yolo:alice" → "alice"
        parts = name.split(":")
        return parts[-1] if len(parts) >= 3 else name
    except Exception:
        return hex_id[:12] + "…"


_GEO_RE = re.compile(
    r"#geo\s+(-?\d{1,3}(?:\.\d+)?)[,\s]+(-?\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)

def _parse_onchain_geo(messages: list[dict]) -> dict[str, dict]:
    """Extract #geo lat,lon announcements from zone messages.

    Takes the *latest* announcement per sender (messages are slot-ascending).
    """
    result: dict[str, dict] = {}
    for m in messages:
        match = _GEO_RE.search(m.get("text", ""))
        if match:
            try:
                lat = float(match.group(1))
                lon = float(match.group(2))
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    result[m["sender"]] = {"lat": lat, "lon": lon, "city": "", "country": ""}
            except ValueError:
                pass
    return result


def _build_geo_index(geo_cache: dict, nodes: list[dict],
                     onchain: dict[str, dict] | None = None) -> dict[str, dict]:
    """Build sender→geo lookup from on-chain #geo announcements,
    falling back to geo_hints.json IP mapping."""
    result: dict[str, dict] = {}

    # Lowest priority: geo_hints.json IP→geo lookup
    hints: dict = {}
    try:
        hints = json.loads(GEO_HINTS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    ip_to_geo = {n["ip"]: n for n in nodes if n.get("ip")}
    for name, ip in hints.items():
        if name.startswith("_"):
            continue
        if ip and ip in ip_to_geo:
            n = ip_to_geo[ip]
            result[name] = {"lat": n["lat"], "lon": n["lon"],
                            "city": n.get("city", ""), "country": n.get("country", "")}
        elif ip and ip in geo_cache:
            g = geo_cache[ip]
            if g.get("lat") and g.get("lon"):
                result[name] = {"lat": g["lat"], "lon": g["lon"],
                                "city": g.get("city", ""), "country": g.get("country", "")}

    # Higher priority: on-chain #geo announcements override
    if onchain:
        result.update(onchain)

    return result


def fetch_zone_messages(geo_cache: dict | None = None,
                        nodes: list[dict] | None = None) -> list[dict]:
    """Read zone-board cache + scanner output. Returns messages newest-first."""
    messages: list[dict] = []
    seen_keys: set[str] = set()

    # Extract #geo announcements solely from the chain scanner (covers all senders)
    onchain_geo: dict[str, dict] = {}
    if ZONE_SCAN_FILE.exists():
        try:
            scan_raw = json.loads(ZONE_SCAN_FILE.read_text())
            onchain_geo = _parse_onchain_geo(scan_raw.get("messages", []))
        except Exception:
            pass

    geo_index = _build_geo_index(geo_cache or {}, nodes or [], onchain=onchain_geo)

    def add_msg(sender: str, text: str, slot: int, block_id: str,
                status: str, observed_at: str, signer: str, tx_id: str = "") -> None:
        text = text.strip()
        if not text or (text.startswith("{") and '"type"' in text):
            return
        key = f"{(block_id or '')[:16]}:{text}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        geo = geo_index.get(sender)
        messages.append({
            "sender":      sender,
            "text":        text,
            "slot":        slot,
            "block_id":    block_id or "",
            "tx_id":       tx_id or "",
            "status":      status,
            "observed_at": observed_at,
            "signer":      signer[:16] if signer else "",
            "live":        "#live" in text.lower(),
            "lat":         geo["lat"]     if geo else None,
            "lon":         geo["lon"]     if geo else None,
            "city":        geo.get("city", "")    if geo else "",
            "country":     geo.get("country", "") if geo else "",
        })

    # 1. Background scanner output (broadest coverage)
    if ZONE_SCAN_FILE.exists():
        try:
            scan = json.loads(ZONE_SCAN_FILE.read_text())
            for m in scan.get("messages", []):
                add_msg(m.get("sender", "?"), m.get("text", ""),
                        m.get("slot", 0), m.get("block_id", ""),
                        "finalized", "", "", m.get("tx_id", ""))
        except Exception as e:
            print(f"  Zone scan error: {e}")

    # 2. Local zone-board dashboard (full metadata, subscribed channels)
    dashboard = ZONE_BOARD_DIR / "dashboard-live-channels.json"
    if dashboard.exists():
        try:
            data = json.loads(dashboard.read_text())
            for sender, msgs in data.get("channels", {}).items():
                for m in msgs:
                    add_msg(sender, m.get("text", ""),
                            m.get("slot", 0), m.get("block_id", ""),
                            m.get("status", "unknown"), m.get("observed_at", ""),
                            m.get("signer", ""))
        except Exception as e:
            print(f"  Zone dashboard error: {e}")

    # 3. Individual channel cache files
    cache_dir = ZONE_BOARD_DIR / "cache"
    if cache_dir.exists():
        for f in sorted(cache_dir.glob("*.json")):
            sender = _channel_hex_to_name(f.stem)
            try:
                raw = json.loads(f.read_text())
                if isinstance(raw, list):
                    for m in raw:
                        add_msg(sender, m.get("text", ""),
                                m.get("slot", 0), m.get("block_id", ""),
                                "confirmed" if not m.get("pending") else "pending",
                                "", "")
            except Exception:
                pass

    messages.sort(key=lambda x: (x["slot"], x["observed_at"]))
    live_count = sum(1 for m in messages if m.get("live"))
    geo_count  = sum(1 for m in messages if m.get("lat") is not None)
    print(f"  Zone messages: {len(messages)} from "
          f"{len(set(m['sender'] for m in messages))} senders "
          f"({live_count} #live, {geo_count} geo-linked)")

    return list(reversed(messages[-100:]))


async def fetch_events(client: httpx.AsyncClient) -> list[dict]:
    """Fetch all upcoming + recent past Logos circle events from Luma."""
    events = []
    for period in ("future", "past"):
        cursor = None
        fetched = 0
        while True:
            params: dict = {
                "calendar_api_id": LUMA_CALENDAR,
                "period": period,
                "pagination_limit": 50,
            }
            if cursor:
                params["pagination_cursor"] = cursor
            try:
                resp = await client.get(
                    "https://api.lu.ma/calendar/get-items",
                    params=params,
                    timeout=10,
                )
                data = resp.json()
            except Exception as e:
                print(f"  Luma error ({period}): {e}")
                break

            for entry in data.get("entries", []):
                ev    = entry.get("event") or {}
                coord = ev.get("coordinate") or {}
                geo   = ev.get("geo_address_info") or {}
                lat   = coord.get("latitude")
                lon   = coord.get("longitude")
                if not lat or not lon:
                    continue
                events.append({
                    "name":     ev.get("name", ""),
                    "start":    ev.get("start_at", ""),
                    "end":      ev.get("end_at", ""),
                    "url":      f"https://lu.ma/{ev.get('url','')}",
                    "lat":      lat,
                    "lon":      lon,
                    "city":     geo.get("city", ""),
                    "country":  geo.get("country", ""),
                    "address":  geo.get("short_address") or geo.get("full_address", ""),
                    "upcoming": period == "future",
                })
                fetched += 1

            if not data.get("has_more") or (period == "past" and fetched >= 100):
                break
            cursor = data.get("next_cursor")

    print(f"  Events: {sum(1 for e in events if e['upcoming'])} upcoming, "
          f"{sum(1 for e in events if not e['upcoming'])} past")
    return events


async def build_network_json() -> dict:
    feed_cache = load_feed_cache()
    db         = load_db()
    geo_cache  = load_geo_cache()
    last_crawl = db.get("last_crawl", 0)
    nodes_raw  = db.get("nodes", {})

    # Collect all IPs, find uncached ones
    ip_map: dict[str, str] = {}   # peer_id → ip
    for pid, node in nodes_raw.items():
        ip = extract_ip(node.get("addrs", []))
        if ip:
            ip_map[pid] = ip

    new_ips = [ip for ip in set(ip_map.values()) if ip not in geo_cache]
    if new_ips:
        print(f"  Geolocating {len(new_ips)} new IPs…")
        fresh = await geolocate_batch(new_ips)
        geo_cache.update(fresh)
        save_geo_cache(geo_cache)
        print(f"  Geo cache now has {len(geo_cache)} entries")

    async with httpx.AsyncClient() as client:
        own_ip          = await get_own_ip(client)
        chain, net_info = await get_chain_info(client)
        events          = await fetch_events(client)
        youtube         = await fetch_latest_youtube(client, feed_cache)
        github          = await fetch_github(client, feed_cache)
        discourse       = await fetch_discourse(client)
        stake           = await fetch_stake(client)
    save_feed_cache(feed_cache)

    # Geolocate own IP if not cached
    if own_ip and own_ip not in geo_cache:
        fresh = await geolocate_batch([own_ip])
        geo_cache.update(fresh)
        save_geo_cache(geo_cache)

    own_peer_id = net_info.get("peer_id", "")

    nodes = []

    # Own node first
    if own_ip:
        g = geo_cache.get(own_ip, {})
        if g.get("lat") and g.get("lon"):
            nodes.append({
                "peer_id":    own_peer_id,
                "ip":         own_ip,
                "lat":        g["lat"],
                "lon":        g["lon"],
                "country":    g.get("country", ""),
                "city":       g.get("city", ""),
                "isp":        g.get("isp", ""),
                "org":        g.get("org", ""),
                "asn":        g.get("as", ""),
                "self":       True,
                "online":     True,
                "first_seen": 0,
                "last_seen":  0,
            })

    seen_ips = {own_ip} if own_ip else set()

    for pid, node in nodes_raw.items():
        ip = ip_map.get(pid)
        if not ip or ip in seen_ips:
            continue
        g = geo_cache.get(ip, {})
        if not g.get("lat") or not g.get("lon"):
            continue
        seen_ips.add(ip)
        nodes.append({
            "peer_id":    pid,
            "ip":         ip,
            "lat":        g["lat"],
            "lon":        g["lon"],
            "country":    g.get("country", ""),
            "city":       g.get("city", ""),
            "isp":        g.get("isp", ""),
            "org":        g.get("org", ""),
            "asn":        g.get("as", ""),
            "self":       False,
            "online":     node.get("last_seen", 0) == last_crawl,
            "first_seen": node.get("first_seen", 0),
            "last_seen":  node.get("last_seen", 0),
        })

    zone_messages = fetch_zone_messages(geo_cache=geo_cache, nodes=nodes)

    return {
        "chain":         chain,
        "network":       net_info,
        "nodes":         nodes,
        "events":        events,
        "youtube":       youtube,
        "github":        github,
        "discourse":     discourse,
        "stake":         stake,
        "zone_messages": zone_messages,
        "updated":       int(time.time()),
    }


def push_to_pages(data: dict):
    PAGES_DIR.mkdir(exist_ok=True)

    # Copy index.html if not already there
    src_html = BASE / "static" / "index.html"
    dst_html = PAGES_DIR / "index.html"
    if src_html.exists():
        dst_html.write_text(src_html.read_text())

    OUT_FILE.write_text(json.dumps(data, indent=2))

    online = sum(1 for n in data["nodes"] if n.get("self") or n.get("online"))
    total  = len(data["nodes"])
    print(f"  Nodes: {online} online / {total} total")

    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    cmds = [
        ["git", "-C", str(PAGES_DIR), "add", "index.html", "network.json"],
        ["git", "-C", str(PAGES_DIR), "commit", "--allow-empty", "-m",
         f"chore: network snapshot {ts} — {online}/{total} nodes online"],
        ["git", "-C", str(PAGES_DIR), "push", "origin", "HEAD:main", "--force"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"  git error: {result.stderr.strip()}")
        else:
            print(f"  {' '.join(cmd[2:4])}: ok")


async def main():
    print(f"[{time.strftime('%H:%M:%S')}] Building network.json…")
    data = await build_network_json()
    print(f"[{time.strftime('%H:%M:%S')}] Pushing to GitHub Pages…")
    push_to_pages(data)
    print(f"[{time.strftime('%H:%M:%S')}] Done.")


if __name__ == "__main__":
    asyncio.run(main())
