#!/usr/bin/env python3
"""
Generate network.json from node_db.json + geo cache and push to GitHub Pages.
Run hourly via cron or: watch -n 3600 python3 publish.py
"""
import asyncio
import datetime
import json
import os
import re
import shutil
import subprocess
import time
from collections import defaultdict
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
ZONE_SCAN_FILE  = Path(os.getenv("ZONE_SCAN_FILE", str(BASE.parent / "zone_scan.json")))
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


LOG_DIR = Path(os.getenv(
    "LOG_DIR",
    os.path.expanduser("~/logos-blockchain-runbook/state/live-v0.1.2/logs"),
))
_PEER_RE  = re.compile(r"\b(12D3KooW[1-9A-HJ-NP-Za-km-z]{20,})\b")
_TS_RE    = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
_ANSI_RE  = re.compile(r"\x1b\[[0-9;]*m")

TELEMETRY_CACHE = BASE / "telemetry_cache.json"


def _hour_bucket(ts_seconds: float) -> int:
    return int(ts_seconds) - (int(ts_seconds) % 3600)


_LOG_FILE_RE = re.compile(r"logos-blockchain\.(\d{4}-\d{2}-\d{2})-(\d{2})$")


def _log_file_ts(path: Path) -> int | None:
    """Return Unix timestamp for a log file based on its name, or None."""
    m = _LOG_FILE_RE.search(path.name)
    if not m:
        return None
    try:
        return int(time.mktime(time.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H")))
    except Exception:
        return None


def compact_logs(keep_hours: int = 12) -> None:
    """Delete log files older than keep_hours. Non-destructive: only removes files
    whose name indicates they are outside the retention window."""
    if not LOG_DIR.exists():
        return
    cutoff = time.time() - keep_hours * 3600
    deleted = 0
    for lf in sorted(LOG_DIR.glob("logos-blockchain.*")):
        ts = _log_file_ts(lf)
        if ts is not None and ts < cutoff:
            try:
                lf.unlink()
                deleted += 1
            except Exception:
                pass
    if deleted:
        print(f"  Log compaction: removed {deleted} files older than {keep_hours}h")


def build_telemetry() -> dict:
    """Parse node logs incrementally — only reads new/grown bytes since last run.

    Cache layout (telemetry_cache.json):
      {
        "files": {"filename": {"size": N, "peers_by_bucket": {"ts_str": ["pid", ...]}}},
        "peer_first": {"pid": ts},
        "peer_last":  {"pid": ts},
      }
    """
    if not LOG_DIR.exists():
        return {}

    from collections import defaultdict

    now     = int(time.time())
    cutoff  = now - 12 * 3600

    # Load incremental cache
    cache: dict = {}
    if TELEMETRY_CACHE.exists():
        try:
            cache = json.loads(TELEMETRY_CACHE.read_text())
        except Exception:
            cache = {}
    file_cache: dict = cache.get("files", {})
    peer_first: dict[str, int] = cache.get("peer_first", {})
    peer_last:  dict[str, int] = cache.get("peer_last", {})

    log_files = [
        lf for lf in sorted(LOG_DIR.glob("logos-blockchain.*"))
        if (ts := _log_file_ts(lf)) is not None and ts >= cutoff
    ]

    files_read = 0
    bytes_read = 0
    for lf in log_files:
        file_ts = _log_file_ts(lf)
        if file_ts is None:
            continue
        bucket    = _hour_bucket(file_ts)
        bkey      = str(bucket)
        cur_size  = lf.stat().st_size
        cached    = file_cache.get(lf.name, {})
        prev_size = cached.get("size", 0)

        if cur_size == prev_size:
            continue  # file hasn't grown — skip

        # Read only the new bytes (seek to where we left off)
        peers_by_bucket: dict[str, list] = cached.get("peers_by_bucket", {})
        new_peers: set[str] = set()
        try:
            with lf.open("rb") as fh:
                fh.seek(prev_size)
                chunk = fh.read()
            bytes_read += len(chunk)
            text = chunk.decode("utf-8", errors="replace")
            for raw_line in text.splitlines():
                line = _ANSI_RE.sub("", raw_line)
                for pid in _PEER_RE.findall(line):
                    new_peers.add(pid)
                    if pid not in peer_first or file_ts < peer_first[pid]:
                        peer_first[pid] = file_ts
                    if pid not in peer_last or file_ts > peer_last[pid]:
                        peer_last[pid] = file_ts
        except Exception:
            continue

        existing = set(peers_by_bucket.get(bkey, []))
        existing.update(new_peers)
        peers_by_bucket[bkey] = list(existing)
        file_cache[lf.name] = {"size": cur_size, "peers_by_bucket": peers_by_bucket}
        files_read += 1

    # Expire cache entries for files outside the 24h window
    active_names = {lf.name for lf in log_files}
    file_cache = {k: v for k, v in file_cache.items() if k in active_names}

    # Persist updated cache
    try:
        TELEMETRY_CACHE.write_text(json.dumps(
            {"files": file_cache, "peer_first": peer_first, "peer_last": peer_last},
            separators=(",", ":"),
        ))
    except Exception:
        pass

    # Aggregate bucket → peer sets across all cached files
    bucket_peers: dict[int, set] = defaultdict(set)
    for fc in file_cache.values():
        for bkey, pids in fc.get("peers_by_bucket", {}).items():
            bucket_peers[int(bkey)].update(pids)

    if not bucket_peers:
        return {}

    end_bucket   = _hour_bucket(now)
    start_bucket = _hour_bucket(cutoff)
    hourly = []
    b = start_bucket
    while b <= end_bucket:
        hourly.append({"ts": b, "count": len(bucket_peers.get(b, set()))})
        b += 3600

    active_latest = len(bucket_peers.get(end_bucket, set()))
    active_peak   = max((len(v) for v in bucket_peers.values()), default=0)

    # Peer uptime: count how many buckets each peer appeared in
    window_hours = (end_bucket - start_bucket) // 3600 + 1
    peer_bucket_count: dict[str, int] = defaultdict(int)
    for peers in bucket_peers.values():
        for pid in peers:
            peer_bucket_count[pid] += 1

    uptime_rows = []
    for pid in sorted(peer_last, key=lambda p: -peer_last[p])[:30]:
        buckets_seen = peer_bucket_count.get(pid, 0)
        uptime_pct   = round(buckets_seen / window_hours * 100, 1) if window_hours else 0
        uptime_rows.append({
            "peer_id":    pid[:20] + "…",
            "uptime_pct": uptime_pct,
            "last_seen":  peer_last[pid],
        })

    MB = bytes_read / 1_048_576
    print(f"  Telemetry: {len(hourly)} hourly buckets, "
          f"{len(peer_first)} unique peers, peak {active_peak}/h "
          f"({files_read} files / {MB:.1f} MB read)")
    return {
        "source": "node-logs",
        "window_hours": window_hours,
        "active_peers_hourly": hourly,
        "summary": {
            "active_latest":      active_latest,
            "active_peak":        active_peak,
            "tracked_peers":      len(peer_first),
            "latest_total_stake": None,
            "stake_points":       0,
        },
        "peer_uptime":            uptime_rows,
        "stake_estimate_hourly": [],
    }


async def fetch_recent_blocks(client: httpx.AsyncClient) -> dict:
    """Fetch recent block sample for the Blocks view."""
    try:
        resp = await client.get(f"{NODE_URL}/cryptarchia/headers?limit=30", timeout=5)
        if resp.status_code != 200:
            return {}
        hashes = resp.json()
    except Exception:
        return {}

    recent = []
    leader_counts: dict[str, int] = {}
    slots = []

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

        header = block.get("header", {})
        slot = header.get("slot", 0)
        lk = header.get("proof_of_leadership", {}).get("leader_key", "")
        txs = block.get("transactions", [])
        ops = sum(len(tx.get("mantle_tx", {}).get("ops", [])) for tx in txs)
        root = header.get("block_root", "")

        slots.append(slot)
        if lk:
            leader_counts[lk] = leader_counts.get(lk, 0) + 1

        recent.append({
            "slot":         slot,
            "leader_key":   lk[:16] if lk else "",
            "transactions": len(txs),
            "operations":   ops,
            "root":         root[:16] if root else "",
            "hash":         h[:16] if h else "",
        })

    total = len(recent)
    total_leader_blocks = sum(leader_counts.values())
    top_leaders = sorted(leader_counts.items(), key=lambda x: -x[1])[:10]

    slot_min = min(slots) if slots else 0
    slot_max = max(slots) if slots else 0
    slot_span = slot_max - slot_min + 1 if slots else 0
    total_txs  = sum(b["transactions"] for b in recent)
    total_ops  = sum(b["operations"]   for b in recent)
    empty_blocks = sum(1 for b in recent if b["transactions"] == 0)
    missed_or_unseen = max(0, slot_span - total)

    print(f"  Blocks: {total} sampled, {len(leader_counts)} unique leaders")
    return {
        "window": {
            "blocks":                total,
            "slot_min":              slot_min,
            "slot_max":              slot_max,
            "slot_span":             slot_span,
            "transactions":          total_txs,
            "operations":            total_ops,
            "empty_blocks":          empty_blocks,
            "missed_or_unseen_slots": missed_or_unseen,
        },
        "leader_diversity": {
            "unique_leaders":       len(leader_counts),
            "top_leader_share_pct": round(100 * top_leaders[0][1] / total_leader_blocks, 1) if top_leaders and total_leader_blocks else 0,
        },
        "top_leaders": [
            {"leader_key": pk[:16], "blocks": n, "pct": round(100 * n / total_leader_blocks, 1) if total_leader_blocks else 0}
            for pk, n in top_leaders
        ],
        "recent": list(reversed(recent[:20])),
    }


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


_YOLO_HEX  = "6c6f676f733a796f6c6f3a"   # "logos:yolo:" as hex
_GAP_BATCH = 2_000                        # slots per API call
_GAP_MIN   = 1_000                        # only gap-fill if lag > this many slots


def _decode_yolo_channel(hex_id: str) -> str:
    """Decode a 32-byte zero-padded channel hex ID to a sender name."""
    try:
        raw = bytes.fromhex(hex_id).rstrip(b"\x00")
        name = raw.decode("utf-8")
        parts = name.split(":")
        return parts[-1] if len(parts) >= 3 else name
    except Exception:
        return hex_id[:12] + "…"


def _scan_local_gap() -> None:
    """If zone_scan.json lags the chain tip by >1000 slots, query the local node
    directly and merge any found zone messages into zone_scan.json.

    Idempotent: deduplicates by (block_id, tx_id, sender, text).
    Called before fetch_zone_messages() on every publish run.
    """
    if not ZONE_SCAN_FILE.exists():
        return
    try:
        scan = json.loads(ZONE_SCAN_FILE.read_text())
    except Exception:
        return

    messages: list[dict] = scan.get("messages", [])
    max_slot = max((m.get("slot", 0) for m in messages), default=0)

    import urllib.request as _ur
    try:
        with _ur.urlopen(f"{NODE_URL}/cryptarchia/info", timeout=5) as r:
            tip_slot: int = json.loads(r.read()).get("slot", 0)
    except Exception as e:
        print(f"  Gap fill: node unreachable ({e})")
        return

    gap = tip_slot - max_slot
    if gap <= _GAP_MIN:
        return

    batches = (gap + _GAP_BATCH - 1) // _GAP_BATCH
    print(f"  Gap fill: {gap} slots behind tip — scanning {batches} batches ({max_slot}→{tip_slot})")

    seen_blocks: set[str] = {m.get("block_id", "") for m in messages if m.get("block_id")}
    new_msgs: list[dict] = []

    slot_lo = max_slot
    while slot_lo < tip_slot:
        slot_hi = min(slot_lo + _GAP_BATCH, tip_slot)
        url = f"{NODE_URL}/cryptarchia/blocks?slot_from={slot_lo}&slot_to={slot_hi}"
        try:
            with _ur.urlopen(url, timeout=15) as r:
                blocks = json.loads(r.read())
        except Exception as e:
            print(f"    Gap fill: error at {slot_lo}–{slot_hi}: {e}")
            slot_lo = slot_hi
            continue

        for block in blocks:
            block_id = (block.get("header") or {}).get("id", "")
            if block_id in seen_blocks:
                continue
            seen_blocks.add(block_id)
            slot = (block.get("header") or {}).get("slot", 0)
            for tx in block.get("transactions", []):
                mantle = tx.get("mantle_tx") or {}
                tx_id = mantle.get("hash", "")
                for op in mantle.get("ops", []):
                    if op.get("opcode") != 17:
                        continue
                    payload = op.get("payload") or {}
                    ch = payload.get("channel_id", "")
                    if not ch.startswith(_YOLO_HEX):
                        continue
                    sender = _decode_yolo_channel(ch)
                    inscription = payload.get("inscription")
                    if not isinstance(inscription, list):
                        continue
                    try:
                        text = bytes(int(b) for b in inscription).decode("utf-8").strip()
                    except Exception:
                        continue
                    if not text or (text.startswith("{") and '"type"' in text):
                        continue
                    new_msgs.append({
                        "sender":   sender, "text": text, "slot": slot,
                        "block_id": block_id, "tx_id": tx_id,
                        "live":     "#live" in text.lower(),
                    })
        slot_lo = slot_hi

    if not new_msgs:
        print("  Gap fill: no new messages found")
        return

    print(f"  Gap fill: +{len(new_msgs)} messages found")
    for m in new_msgs:
        print(f"    [{m['slot']}] {m['sender']}: {m['text'][:60]}")

    # Merge and dedup by (block_id, tx_id, sender, text)
    all_msgs = messages + new_msgs
    seen_keys: set[tuple] = set()
    deduped: list[dict] = []
    for m in sorted(all_msgs, key=lambda x: x.get("slot", 0)):
        key = (m.get("block_id", ""), m.get("tx_id", ""), m.get("sender", ""), m.get("text", ""))
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(m)

    scan["messages"] = deduped
    ZONE_SCAN_FILE.write_text(json.dumps(scan, indent=2))
    print(f"  Gap fill: zone_scan.json updated ({len(deduped)} total messages)")


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
    """Read zone-board cache + scanner output. Returns messages newest-first.

    Priority:
      1. Finalized (zone_scan.json) — sorted by blockchain slot DESC
      2. Pending  (dashboard/cache) — sorted by observed_at DESC, only if not
         already in the finalized set (dedup key uses 120-char text prefix to
         handle dashboard truncation).
    """
    # ── 1. Finalized messages from zone scanner ───────────────────────────────
    finalized_raw: list[dict] = []
    if ZONE_SCAN_FILE.exists():
        try:
            scan = json.loads(ZONE_SCAN_FILE.read_text())
            finalized_raw = scan.get("messages", [])
        except Exception as e:
            print(f"  Zone scan error: {e}")

    # Key set for cross-source dedup — 120-char prefix handles dashboard truncation
    finalized_keys: set[str] = {
        f"{m.get('sender', '')}:{m.get('text', '')[:120]}"
        for m in finalized_raw
    }
    onchain_geo = _parse_onchain_geo(finalized_raw)
    geo_index = _build_geo_index(geo_cache or {}, nodes or [], onchain=onchain_geo)

    # ── 2. Pending from dashboard + cache (not already finalized) ─────────────
    pending_raw: list[dict] = []
    seen_pending: set[str] = set()

    def _add_pending(sender: str, text: str, status: str,
                     observed_at: str, signer: str, block_id: str) -> None:
        text = text.strip()
        if not text or (text.startswith("{") and '"type"' in text):
            return
        if f"{sender}:{text[:120]}" in finalized_keys:
            return
        key = f"{sender}:{text}"
        if key in seen_pending:
            return
        seen_pending.add(key)
        pending_raw.append({
            "sender": sender, "text": text, "status": status,
            "observed_at": observed_at, "signer": signer, "block_id": block_id,
        })

    dashboard = ZONE_BOARD_DIR / "dashboard-live-channels.json"
    if dashboard.exists():
        try:
            data = json.loads(dashboard.read_text())
            for sender, msgs in data.get("channels", {}).items():
                for m in msgs:
                    _add_pending(sender, m.get("text", ""),
                                 m.get("status", "pending"), m.get("observed_at", ""),
                                 m.get("signer", ""), m.get("block_id", ""))
        except Exception as e:
            print(f"  Zone dashboard error: {e}")

    cache_dir = ZONE_BOARD_DIR / "cache"
    if cache_dir.exists():
        for f in sorted(cache_dir.glob("*.json")):
            sender = _channel_hex_to_name(f.stem)
            try:
                raw = json.loads(f.read_text())
                if isinstance(raw, list):
                    for m in raw:
                        _add_pending(sender, m.get("text", ""),
                                     "confirmed" if not m.get("pending") else "pending",
                                     "", "", m.get("block_id", ""))
            except Exception:
                pass

    # Drop stale pending (>7 days old) and "(pending…)" duplicates
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    clean_pending_texts = {m["text"] for m in pending_raw
                           if not m["text"].endswith(" (pending…)")}
    fresh_pending: list[dict] = []
    for m in pending_raw:
        if m["text"].endswith(" (pending…)") and \
                m["text"].removesuffix(" (pending…)") in clean_pending_texts:
            continue
        obs = m.get("observed_at", "")
        if obs:
            try:
                if datetime.datetime.fromisoformat(obs) <= cutoff:
                    continue
            except ValueError:
                pass
        fresh_pending.append(m)

    # ── 3. Enrich with geo and build output ───────────────────────────────────
    def _enrich(sender: str, text: str, slot: int, slot_unknown: bool,
                block_id: str, tx_id: str, status: str,
                observed_at: str, signer: str) -> dict:
        geo = geo_index.get(sender)
        return {
            "sender":       sender,
            "text":         text,
            "slot":         slot,
            "slot_unknown": slot_unknown,
            "block_id":     block_id,
            "tx_id":        tx_id,
            "status":       status,
            "observed_at":  observed_at,
            "signer":       signer[:16] if signer else "",
            "live":         "#live" in text.lower(),
            "lat":          geo["lat"]          if geo else None,
            "lon":          geo["lon"]          if geo else None,
            "city":         geo.get("city", "") if geo else "",
            "country":      geo.get("country", "") if geo else "",
        }

    # Finalized: blockchain slot DESC (newest on-chain message first)
    finalized_sorted = sorted(finalized_raw, key=lambda x: x.get("slot", 0), reverse=True)
    finalized_out = [
        _enrich(m.get("sender", "?"), m.get("text", ""), m.get("slot", 0),
                False, m.get("block_id", ""), m.get("tx_id", ""), "finalized", "", "")
        for m in finalized_sorted[:100]
    ]

    # Pending: observed_at DESC (most recently seen first)
    pending_sorted = sorted(fresh_pending, key=lambda x: x.get("observed_at", ""), reverse=True)
    pending_out = [
        _enrich(m["sender"], m["text"], 0, True,
                m.get("block_id", ""), "", m["status"],
                m.get("observed_at", ""), m.get("signer", ""))
        for m in pending_sorted[:20]
    ]

    messages = finalized_out + pending_out
    live_count = sum(1 for m in messages if m.get("live"))
    geo_count  = sum(1 for m in messages if m.get("lat") is not None)
    print(f"  Zone messages: {len(finalized_out)} finalized + {len(pending_out)} pending "
          f"({live_count} #live, {geo_count} geo-linked)")
    return messages


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
        recent_blocks   = await fetch_recent_blocks(client)
    compact_logs()
    telemetry = build_telemetry()
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
                "self":       False,
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
            "peer_id":     pid,
            "ip":          ip,
            "lat":         g["lat"],
            "lon":         g["lon"],
            "country":     g.get("country", ""),
            "city":        g.get("city", ""),
            "isp":         g.get("isp", ""),
            "org":         g.get("org", ""),
            "asn":         g.get("as", ""),
            "environment": node.get("environment", ""),
            "self":        False,
            "online":      node.get("last_seen", 0) == last_crawl,
            "first_seen":  node.get("first_seen", 0),
            "last_seen":   node.get("last_seen", 0),
        })

    _scan_local_gap()
    zone_messages = fetch_zone_messages(geo_cache=geo_cache, nodes=nodes)

    # /data disk usage
    disk: dict = {}
    try:
        du = shutil.disk_usage("/data")
        disk = {
            "total_gb":  round(du.total / 1e9, 1),
            "used_gb":   round(du.used  / 1e9, 1),
            "free_gb":   round(du.free  / 1e9, 1),
            "used_pct":  round(du.used  / du.total * 100, 1),
        }
        print(f"  Disk /data: {disk['free_gb']} GB free ({disk['used_pct']}% used)")
    except Exception as e:
        print(f"  Disk check failed: {e}")

    # Approximate genesis timestamp: current unix time minus current tip slot (1 slot ≈ 1s).
    # Used by the frontend to convert slot numbers to human-readable relative times.
    genesis_ts = int(time.time()) - chain.get("slot", 0) if chain.get("slot") else None

    return {
        "chain":            chain,
        "network":          net_info,
        "nodes":            nodes,
        "events":           events,
        "youtube":          youtube,
        "github":           github,
        "discourse":        discourse,
        "stake":            stake,
        "recent_blocks":    recent_blocks,
        "telemetry":        telemetry,
        "zone_messages":    zone_messages,
        "genesis_ts":       genesis_ts,
        "disk":             disk,
        "peer_data_source": "crawler",
        "peer_snapshot":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated":          int(time.time()),
    }


def push_to_pages(data: dict):
    PAGES_DIR.mkdir(exist_ok=True)

    # Copy static assets
    static = BASE / "static"
    for name in ("index.html", "logo.svg", "social.png"):
        src = static / name
        if src.exists():
            (PAGES_DIR / name).write_bytes(src.read_bytes())

    # Ensure CNAME is always present so the custom domain survives force-pushes
    cname = PAGES_DIR / "CNAME"
    if not cname.exists():
        cname.write_text("logos.live\n")

    OUT_FILE.write_text(json.dumps(data, indent=2))

    online = sum(1 for n in data["nodes"] if n.get("self") or n.get("online"))
    total  = len(data["nodes"])
    print(f"  Nodes: {online} online / {total} total")

    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    cmds = [
        ["git", "-C", str(PAGES_DIR), "add", "index.html", "network.json", "CNAME", "logo.svg", "social.png"],
        ["git", "-C", str(PAGES_DIR), "commit", "--allow-empty", "-m",
         f"chore: network snapshot {ts} — {online}/{total} nodes online"],
        ["git", "-C", str(PAGES_DIR), "push", "origin", "HEAD:gh-pages", "--force"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            print(f"  git error: {result.stderr.strip()}")
        else:
            print(f"  {' '.join(cmd[2:4])}: ok")


def resync_zone_board():
    """Trigger zone-board /resync so newly finalized messages appear in the next snapshot."""
    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", "zone-board", "/resync", "Enter"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            print("  zone-board: /resync sent")
        else:
            print(f"  zone-board: resync skipped ({result.stderr.strip() or 'session not found'})")
    except Exception as e:
        print(f"  zone-board: resync skipped ({e})")


async def main():
    # Resync zone-board first so any newly finalized messages are in the dashboard
    # before we read it. Sleep briefly to let zone-board process the response.
    resync_zone_board()
    await asyncio.sleep(8)
    print(f"[{time.strftime('%H:%M:%S')}] Building network.json…")
    data = await build_network_json()
    print(f"[{time.strftime('%H:%M:%S')}] Pushing to GitHub Pages…")
    push_to_pages(data)
    print(f"[{time.strftime('%H:%M:%S')}] Done.")


if __name__ == "__main__":
    asyncio.run(main())
