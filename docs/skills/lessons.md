# Lessons — docs/skills/lessons.md

Numbered lessons. Grep this file when hitting a bug or surprising behavior.

---

1. **Crawler health threshold is 30 minutes.** `server.py` considers `peers.json` "fresh" only if it is < 30 min old. If the crawler stops, the dashboard shows stale data silently. Check crawler health via `/api/agent/crawler/status`.

2. **`pages/` is a git worktree — never push from repo root.** The `gh-pages` branch is managed exclusively by `publish.py`. Running `git push` from the repo root will not update GitHub Pages and may corrupt the worktree state.

3. **`NODE_URL` unset = feedback mode.** When `NODE_URL` is not set, `server.py` and `publish.py` fall back to mock/feedback data. This is intentional for local dev. Set `NODE_URL=http://127.0.0.1:8085` for live data.

4. **Log compaction runs inside `publish.py`, not only logrotate.** `publish.py` deletes node log files older than 12 hours from `LOG_DIR`. Logrotate handles crawler/scanner logs separately (7-day retention). Two separate retention policies coexist.

5. **`zone_scan_state.json` tracks scanner chain position — never edit manually.** Manual edits corrupt the scanner's understanding of where it left off. If state is wrong, delete the file entirely to trigger a full rescan (expensive but safe).

6. **`telemetry_cache.json` tracks incremental log parse position.** Deleting it causes `telemetry_collector.py` (called from `publish.py`) to reprocess all log files from the beginning. Useful for fixing wrong counts, but slow on large log dirs.

7. **ip-api.com batch size limit is 100 IPs per request.** `publish.py` batches geo lookups in chunks of 100. Exceeding this silently truncates results or triggers rate limiting. The geo_cache grows indefinitely — cache hits bypass the API.

8. **`geo_cache.json` missing entries cause nodes to be silently dropped from the map.** If an IP has no cached geolocation and the ip-api.com lookup fails, that peer simply does not appear on the map. No error is surfaced to the user.

9. **Peer window label was changed to 12h (commit 8a56278).** The telemetry peer window in the frontend shows "12h" — previous label was different. If telemetry chart labels look wrong, check this commit for context.

10. **Telemetry retention was reduced to 12h (commit 8a56278) to address gossipsub queue overflow.** Long retention caused log queue buildup. If node logs grow very fast again, investigate gossipsub message rate before increasing retention.

11. **`publish.py` runs on Sneg, not Wild.** The cron is on `sher@sneg`. Running `publish.py` locally on Wild without `NODE_URL` pointing to a live node will produce feedback-mode output only. Do not push that to pages/.

12. **GitHub token lives in `~/.env.anqa` on Sneg.** The publish cron sources this file before running. If the token expires or is rotated, update `~/.env.anqa` on Sneg and restart the cron. Never commit the token.
