# Lessons — docs/skills/lessons.md

Numbered lessons. Grep this file when hitting a bug or surprising behavior.

---

1. **Crawler health threshold is 30 minutes.** `server.py` considers `peers.json` "fresh" only if it is < 30 min old. If the crawler stops, the dashboard shows stale data silently. Check crawler health via `/api/agent/crawler/status`.

2. **`pages/` is a git worktree — never push from repo root.** The `gh-pages` branch is managed exclusively by `publish.py`. Running `git push` from the repo root will not update GitHub Pages and may corrupt the worktree state.

3. **`NODE_URL` unset = feedback mode.** When `NODE_URL` is not set, `server.py` and `publish.py` fall back to mock/feedback data. This is intentional for local dev. Set `NODE_URL=http://127.0.0.1:8080` for live data.

4. **Log compaction runs inside `publish.py`, not only logrotate.** `compact_logs(keep_hours=168)` deletes node log files older than 7 days. The default parameter is 12h but the actual call uses 168h. Telemetry window (`build_telemetry`) also uses 168h. Logrotate handles crawler/scanner logs separately. Do not confuse the default with the actual.

5. **`zone_scan_state.json` tracks scanner chain position — never edit manually.** Manual edits corrupt the scanner's understanding of where it left off. If state is wrong, delete the file entirely to trigger a full rescan (expensive but safe).

6. **`telemetry_cache.json` tracks incremental log parse position.** Deleting it causes `telemetry_collector.py` (called from `publish.py`) to reprocess all log files from the beginning. Useful for fixing wrong counts, but slow on large log dirs.

7. **ip-api.com batch size limit is 100 IPs per request.** `publish.py` batches geo lookups in chunks of 100. Exceeding this silently truncates results or triggers rate limiting. The geo_cache grows indefinitely — cache hits bypass the API.

8. **`geo_cache.json` missing entries cause nodes to be silently dropped from the map.** If an IP has no cached geolocation and the ip-api.com lookup fails, that peer simply does not appear on the map. No error is surfaced to the user.

9. **Peer window label was changed to 12h (commit 8a56278).** The telemetry peer window in the frontend shows "12h" — previous label was different. If telemetry chart labels look wrong, check this commit for context.

10. **Telemetry retention is 168h (7 days), not 12h.** Was previously reduced to 12h (commit 8a56278) for gossipsub queue overflow reasons, then extended to 168h. If node logs grow fast again, investigate gossipsub message rate before cutting retention. The `compact_logs` default parameter (12h) is misleading — always check the call site.

11. **`publish.py` runs on Sneg, not Wild.** The cron is on `sher@sneg`. Running `publish.py` locally on Wild without `NODE_URL` pointing to a live node will produce feedback-mode output only. Do not push that to pages/.

13. **Read the code before making claims about how the system works.** Fergie drafted an external reply stating "we compact logs to 12h" without reading publish.py first. The actual value was 168h. Rule: any claim about timing, retention, thresholds, or behavior must be verified against the source before being communicated externally.

12. **GitHub token lives in `~/.env.anqa` on Sneg.** The publish cron sources this file before running. If the token expires or is rotated, update `~/.env.anqa` on Sneg and restart the cron. Never commit the token.

14. **libp2p multiaddr format is `proto/port`, not `port/proto`.** `/ip4/HOST/tcp/4001/p2p/PEERID` — protocol name comes before the port number. Writing `\d+/\S+` when you need `\w+/\d+` in a regex is a common mistake. Always verify against a real log line before writing a parsing pattern.

15. **Qwen3 thinking mode exhausts token budget silently.** When Qwen3-27b has reasoning enabled, it spends all tokens on `<think>` content and returns empty `content`. Bypass: use `/v1/completions` endpoint with `<think>\n</think>` as assistant prefill to skip the reasoning phase entirely.

16. **Cache pruning must run after the variable it depends on is defined.** Inserting a pruning block that references `bucket_peers` before the loop that builds `bucket_peers` causes `UnboundLocalError` at runtime. Always trace data flow through the function before adding cleanup code.

17. **`heard_count` should equal `len(heard_nodes)`, not a count of raw log IPs.** Some log IPs have no geo coords (geo lookup failed or IP is in geo_cache with nulls). Counting raw IPs overcounts visually displayed nodes. Build `heard_nodes` first (log IPs with valid lat/lon + not in crawler DB), then set `heard_count = len(heard_nodes)`.

18. **CSS grid `repeat(N, ...)` must match actual child count.** Adding a metric to a status strip without updating the column count causes the new item to wrap to a second row. Always update the template-columns value when adding or removing grid children.

19. **v0.2 `/cryptarchia/info` is reshaped — always flatten it.** Fields moved under a `cryptarchia_info` wrapper (so top-level `slot`/`tip` are gone) and `mode` is now an object `{"Started":"Online"}`, not a string. Use `flatten_chain_info()` (in both `publish.py` and `server.py`) at every read; it unwraps the nesting and collapses `mode` to a string, and surfaces the new `lib_slot` finalization anchor (a block/message is finalized when `slot ≤ lib_slot`). Full diff: `docs/plans/v0.2-api-diff.md`.

20. **v0.2 block-by-hash is `GET /cryptarchia/blocks/{hash}` — the old ways 404/empty.** `POST /storage/block` returns 404 and `GET /cryptarchia/blocks?slot_from=&slot_to=` returns `[]` on v0.2. Fetch blocks by hash and walk the chain backward via `header.parent_block` (the zone-scanner does a unified parent-walk from tip to the first seen block — `seen_blocks` is the resume mechanism). `header.parent_block` and `lib`/`lib_slot` are new fields; `header.{id,slot,block_root,proof_of_leadership.leader_key}` and `transactions[].mantle_tx.ops[]` are unchanged. opcode 17 is still ChannelInscribe; `inscription` is now a hex string (or int array on older nodes); skip `/LEZ/` system inscriptions.

21. **The DHT kad protocol id did NOT change across the v0.1.2→v0.2 fork.** It is still `/logos-blockchain-testnet-v0.1.2/kad/1.0.0` (verified: the crawler finds live v0.2 peers with that compiled default and no `KAD_PROTOCOL` override). Do NOT "modernize" the string to v0.2 — that breaks discovery. Lesson: verify protocol/format identity against the live system; never infer a break from a version bump. Bootstrap peers/addresses are also unchanged.

22. **zone-scanner uses rustls, not openssl.** Sneg has no system OpenSSL dev headers, so `openssl-sys` (reqwest's default native-tls) fails to build there. The scanner only talks HTTP to a local node, so `Cargo.toml` pins `reqwest = { default-features = false, features = ["json", "rustls-tls"] }`. The crawler already builds rustls fine on Sneg. Build Rust on Sneg via `~/.cargo/bin/cargo` (rustc 1.96).

23. **v0.2 peer telemetry lives in journald, not log files.** The v0.2 node streams networking/consensus logs to systemd journald (`logos-node-v2` unit); `~/logos-v2/standalone/MyLogFile.log` holds only circuit `[TRACE]` output. Set `NODE_LOG_UNIT=logos-node-v2` — then `publish.py` (`_build_telemetry_journald`) and `server.py` (`_read_log_telemetry`/`_parse_peers`) read `journalctl --user -u <unit> --grep 12D3KooW` and bucket peer observations by the **line** ISO timestamp (not the filename). `journalctl --user` works in cron's minimal env with no `XDG_RUNTIME_DIR`. On Sneg it's set via a `logos-live.service.d/override.conf` drop-in and in the publish cron line.

24. **The scanner dead-locks on stale pre-fork state after a chain fork.** `zone_scan_state.json` carries `scanned_tip` from the *old* chain (e.g. 3.9M) while the new v0.2 tip is far lower (~350k); the watch loop skips forever because `new_tip ≤ scanned_tip`. On any fork/genesis reset, delete `zone_scan_state.json` (never hand-edit) so the scanner re-walks from the live tip. The new parent-walk scanner also self-heals via `seen_blocks` once state is cleared. (Root-causes the old "scanned_to stays 0" open item.)
