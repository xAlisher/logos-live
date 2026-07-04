/// Zone-board block scanner
///
/// Walks the Logos v0.2 chain backward from the current tip by following each
/// block's `header.parent_block` pointer, extracts opcode=17 (ChannelInscribe)
/// inscriptions on `logos:yolo:*` channels, and accumulates them in
/// zone_scan.json. Runs continuously: every poll it walks from the current tip
/// back to the last block it has already seen — so the first pass reaches
/// genesis and later passes only cover blocks added since.
///
/// v0.2 API notes (see docs/plans/v0.2-api-diff.md):
///   - /cryptarchia/info nests fields under `cryptarchia_info` (tip, slot).
///   - block-by-hash is GET /cryptarchia/blocks/{hash} (the old
///     ?slot_from=&slot_to= form returns [] and POST /storage/block 404s).
///   - inscription payloads are a hex string on current nodes (int array on
///     older ones); channel_id keeps the `logos:yolo:` prefix scheme.
use std::{collections::HashMap, collections::HashSet, time::Duration};

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};
use tokio::time::sleep;

// ── Config ────────────────────────────────────────────────────────────────────
fn node_url() -> String {
    std::env::var("NODE_URL").unwrap_or_else(|_| "http://127.0.0.1:8080".into())
}
/// "logos:yolo:" as hex prefix (channel IDs are 32-byte zero-padded)
const YOLO_HEX: &str = "6c6f676f733a796f6c6f3a";
/// Seconds to wait between tip polls once caught up.
const POLL_INTERVAL_SECS: u64 = 30;
/// Persist progress every N blocks during a long backward walk.
const SAVE_EVERY_BLOCKS: usize = 500;
/// Output file (relative to cwd, override via ZONE_SCAN_FILE env var)
fn out_file() -> String {
    std::env::var("ZONE_SCAN_FILE").unwrap_or_else(|_| "../zone_scan.json".into())
}

/// A hash that terminates the parent walk (genesis / missing parent).
fn is_terminal_hash(hash: &str) -> bool {
    hash.is_empty() || hash.chars().all(|c| c == '0')
}

/// Load channel_hints.json: maps random channel_id hex → display name.
fn load_channel_hints(msgs_path: &str) -> HashMap<String, String> {
    let hints_path = {
        let p = std::path::Path::new(msgs_path);
        p.parent()
            .unwrap_or(std::path::Path::new("."))
            .join("channel_hints.json")
    };
    let Ok(text) = std::fs::read_to_string(&hints_path) else {
        return HashMap::new();
    };
    let Ok(map) = serde_json::from_str::<serde_json::Value>(&text) else {
        return HashMap::new();
    };
    map.as_object()
        .map(|obj| {
            obj.iter()
                .filter(|(k, _)| !k.starts_with('_'))
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default()
}

/// Resolve channel_id to a sender name.
/// Old/current format: hex-encoded "logos:yolo:<name>" → decode.
/// Otherwise: random key hash → look up in hints, fall back to first 8 hex chars.
fn resolve_channel(ch: &str, hints: &HashMap<String, String>) -> Option<String> {
    if ch.starts_with(YOLO_HEX) {
        return Some(decode_yolo_channel(ch));
    }
    Some(
        hints
            .get(ch)
            .cloned()
            .unwrap_or_else(|| ch.chars().take(8).collect()),
    )
}

/// Derive the state file path from the messages file path.
fn state_file(msgs_path: &str) -> String {
    if let Some(stem) = msgs_path.strip_suffix(".json") {
        format!("{stem}_state.json")
    } else {
        format!("{msgs_path}_state")
    }
}

// ── Data model ────────────────────────────────────────────────────────────────
#[derive(Serialize, Deserialize, Clone, Debug)]
struct ZoneMessage {
    sender: String,
    text: String,
    slot: u64,
    block_id: String,
    tx_id: String,
    /// True if text contains "#live"
    live: bool,
}

#[derive(Default)]
struct ScanDb {
    /// Messages found, sorted by slot ascending
    messages: Vec<ZoneMessage>,
    /// Block hashes we have already processed (walk stops when it reaches one)
    seen_blocks: HashSet<String>,
    /// Lowest slot we have scanned down to (informational)
    scanned_to: u64,
    /// Highest tip slot we have seen (informational)
    scanned_tip: u64,
}

/// Serializable messages-only view — written to zone_scan.json.
/// External tools (gap fills, manual edits) can safely modify this file;
/// scanner saves never clobber it.
#[derive(Serialize, Deserialize, Default)]
struct ScanMessages {
    messages: Vec<ZoneMessage>,
}

/// Internal scanner bookkeeping — written to zone_scan_state.json.
#[derive(Serialize, Deserialize, Default)]
struct ScanState {
    seen_blocks: HashSet<String>,
    scanned_to: u64,
    scanned_tip: u64,
}

// ── Main ──────────────────────────────────────────────────────────────────────
#[tokio::main]
async fn main() -> Result<()> {
    let path = out_file();
    eprintln!("Zone scanner starting. Output: {path}");
    eprintln!("Node: {}", node_url());

    let hints = load_channel_hints(&path);
    eprintln!("Channel hints loaded: {} entries", hints.len());

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()?;

    let mut db = load_db(&path);
    eprintln!(
        "Loaded {} messages, {} seen blocks.",
        db.messages.len(),
        db.seen_blocks.len()
    );

    // Unified walk: every poll, follow parent pointers from the current tip back
    // to the first block we have already seen (or genesis). The seen-block set is
    // the resume mechanism — first pass reaches genesis, later passes cover only
    // the blocks added since the previous tip.
    loop {
        match walk_from_tip(&client, &mut db, &hints, &path).await {
            Ok(added) if added > 0 => {
                eprintln!("Walk complete — +{added} new zone messages ({} total).", db.messages.len());
            }
            Ok(_) => {}
            Err(e) => eprintln!("Walk error: {e}"),
        }
        sleep(Duration::from_secs(POLL_INTERVAL_SECS)).await;
    }
}

/// Walk from the current tip backward via `parent_block`, collecting zone
/// messages from every not-yet-seen block. Returns the number of new messages.
async fn walk_from_tip(
    client: &reqwest::Client,
    db: &mut ScanDb,
    hints: &HashMap<String, String>,
    path: &str,
) -> Result<usize> {
    let (tip_slot, tip_hash) = get_tip(client).await?;
    db.scanned_tip = db.scanned_tip.max(tip_slot);

    if is_terminal_hash(&tip_hash) {
        return Err(anyhow!("node reported empty tip hash"));
    }
    if db.seen_blocks.contains(&tip_hash) {
        return Ok(0); // already at the current tip
    }

    let mut cursor = tip_hash;
    let mut added = 0usize;
    let mut since_save = 0usize;
    let mut lowest_slot = u64::MAX;

    while !is_terminal_hash(&cursor) && !db.seen_blocks.contains(&cursor) {
        let block = match fetch_block(client, &cursor).await {
            Ok(Some(b)) => b,
            Ok(None) => break, // 404 — parent not in store; stop this walk
            Err(e) => {
                eprintln!("  block {cursor:.12} fetch error: {e}; will retry next poll");
                break;
            }
        };
        db.seen_blocks.insert(cursor.clone());

        let slot = block["header"]["slot"].as_u64().unwrap_or(0);
        lowest_slot = lowest_slot.min(slot);

        let msgs = extract_block_messages(&block, hints);
        if !msgs.is_empty() {
            for m in &msgs {
                eprintln!(
                    "  [{slot}] {sender}: {text}",
                    slot = m.slot,
                    sender = m.sender,
                    text = preview_text(&m.text, 80)
                );
            }
            added += msgs.len();
            db.messages.extend(msgs);
            dedup_messages(&mut db.messages);
        }

        let parent = block["header"]["parent_block"]
            .as_str()
            .unwrap_or("")
            .to_string();

        since_save += 1;
        if since_save >= SAVE_EVERY_BLOCKS {
            save_db(path, db);
            since_save = 0;
            eprintln!("  … {} blocks seen, {} messages so far", db.seen_blocks.len(), db.messages.len());
        }
        cursor = parent;
    }

    if lowest_slot != u64::MAX {
        db.scanned_to = lowest_slot;
    }
    save_db(path, db);
    Ok(added)
}

// ── Node API helpers ──────────────────────────────────────────────────────────
/// GET /cryptarchia/info → (tip_slot, tip_hash). Handles the v0.2 nested
/// `cryptarchia_info` wrapper and the older flat shape.
async fn get_tip(client: &reqwest::Client) -> Result<(u64, String)> {
    let v: serde_json::Value = client
        .get(format!("{}/cryptarchia/info", node_url()))
        .send()
        .await?
        .json()
        .await?;
    let info = v.get("cryptarchia_info").unwrap_or(&v);
    let slot = info["slot"]
        .as_u64()
        .ok_or_else(|| anyhow!("cryptarchia/info missing slot"))?;
    let tip = info["tip"].as_str().unwrap_or("").to_string();
    Ok((slot, tip))
}

/// GET /cryptarchia/blocks/{hash} → full block object, or None on 404.
async fn fetch_block(client: &reqwest::Client, hash: &str) -> Result<Option<serde_json::Value>> {
    let resp = client
        .get(format!("{}/cryptarchia/blocks/{hash}", node_url()))
        .send()
        .await?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Ok(None);
    }
    let resp = resp.error_for_status()?;
    Ok(Some(resp.json().await?))
}

/// Extract all zone messages (opcode 17 inscriptions) from one block.
fn extract_block_messages(
    block: &serde_json::Value,
    hints: &HashMap<String, String>,
) -> Vec<ZoneMessage> {
    let block_id = block["header"]["id"].as_str().unwrap_or("").to_string();
    let slot = block["header"]["slot"].as_u64().unwrap_or(0);

    let mut msgs = Vec::new();
    let Some(txs) = block["transactions"].as_array() else {
        return msgs;
    };
    for tx in txs {
        let tx_id = tx["mantle_tx"]["hash"].as_str().unwrap_or("").to_string();
        let Some(ops) = tx["mantle_tx"]["ops"].as_array() else {
            continue;
        };
        for op in ops {
            if op["opcode"].as_u64() != Some(17) {
                continue;
            }
            let ch = op["payload"]["channel_id"].as_str().unwrap_or("");

            // Inscription bytes: hex string (current nodes) or int array (older).
            let insc = &op["payload"]["inscription"];
            let raw: Option<Vec<u8>> = if let Some(arr) = insc.as_array() {
                Some(arr.iter().filter_map(|v| v.as_u64().map(|b| b as u8)).collect())
            } else if let Some(s) = insc.as_str() {
                hex::decode(s).ok()
            } else {
                None
            };
            // Binary / non-UTF8 inscriptions are system ops — skip.
            let text = match raw.as_ref().and_then(|b| std::str::from_utf8(b).ok()) {
                Some(s) => s.trim().to_string(),
                None => continue,
            };
            if text.is_empty() {
                continue;
            }
            // Skip system/program inscriptions (LEZ clock/program accounts, typed
            // JSON control messages) — these ride opcode 17 too but aren't zone-board.
            if text.contains("/LEZ/") {
                continue;
            }
            if text.starts_with('{') && text.contains("\"type\"") {
                continue;
            }

            let sender = match resolve_channel(ch, hints) {
                Some(s) => s,
                None => continue,
            };

            let live = text.to_lowercase().contains("#live");
            msgs.push(ZoneMessage {
                sender,
                text,
                slot,
                block_id: block_id.clone(),
                tx_id: tx_id.clone(),
                live,
            });
        }
    }
    msgs
}

fn decode_yolo_channel(hex: &str) -> String {
    let Ok(bytes) = hex::decode(hex) else {
        return preview_text(hex, 12);
    };
    let trimmed = bytes
        .iter()
        .rev()
        .skip_while(|&&b| b == 0)
        .cloned()
        .collect::<Vec<_>>();
    let trimmed: Vec<u8> = trimmed.into_iter().rev().collect();
    let s = String::from_utf8_lossy(&trimmed).to_string();
    // "logos:yolo:alice" → "alice"
    s.split(':').last().unwrap_or(&s).to_string()
}

fn message_key(message: &ZoneMessage) -> (String, String, String, String) {
    (
        message.block_id.clone(),
        message.tx_id.clone(),
        message.sender.clone(),
        message.text.clone(),
    )
}

fn dedup_messages(messages: &mut Vec<ZoneMessage>) {
    messages.sort_by_key(|m| (m.slot, message_key(m)));
    messages.dedup_by_key(|m| message_key(m));
}

fn preview_text(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

// ── Persistence ───────────────────────────────────────────────────────────────
fn load_db(path: &str) -> ScanDb {
    // Messages file — externally editable; gap fills added here survive scanner saves.
    let msgs: ScanMessages = std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();

    // State file — internal scanner bookkeeping.
    let state: ScanState = std::fs::read_to_string(&state_file(path))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();

    ScanDb {
        messages:    msgs.messages,
        seen_blocks: state.seen_blocks,
        scanned_to:  state.scanned_to,
        scanned_tip: state.scanned_tip,
    }
}

fn save_db(path: &str, db: &ScanDb) {
    // Write messages-only file — safe for external edits and gap fills.
    let msgs = ScanMessages { messages: db.messages.clone() };
    if let Ok(json) = serde_json::to_string_pretty(&msgs) {
        let _ = std::fs::write(path, json);
    }

    // Write internal state separately — scanner saves never clobber the messages file.
    let state = ScanState {
        seen_blocks: db.seen_blocks.clone(),
        scanned_to:  db.scanned_to,
        scanned_tip: db.scanned_tip,
    };
    if let Ok(json) = serde_json::to_string_pretty(&state) {
        let _ = std::fs::write(state_file(path), json);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(block_id: &str, tx_id: &str, sender: &str, text: &str, slot: u64) -> ZoneMessage {
        ZoneMessage {
            block_id: block_id.to_string(),
            tx_id: tx_id.to_string(),
            sender: sender.to_string(),
            text: text.to_string(),
            slot,
            live: false,
        }
    }

    #[test]
    fn dedup_messages_keeps_multiple_transactions_in_one_block() {
        let mut messages = vec![
            msg("block-a", "tx-a", "alice", "hello", 10),
            msg("block-a", "tx-b", "bob", "world", 10),
            msg("block-a", "tx-a", "alice", "hello", 10),
        ];

        dedup_messages(&mut messages);

        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].tx_id, "tx-a");
        assert_eq!(messages[1].tx_id, "tx-b");
    }

    #[test]
    fn is_terminal_hash_detects_genesis_and_empty() {
        assert!(is_terminal_hash(""));
        assert!(is_terminal_hash(&"0".repeat(64)));
        assert!(!is_terminal_hash("4024eca38c5e"));
    }

    #[test]
    fn extract_reads_hex_string_inscription_opcode_17() {
        // "logos:yolo:alice" as channel, "hi #live" as a hex-string inscription.
        let channel_hex = hex::encode(b"logos:yolo:alice");
        let insc_hex = hex::encode("hi #live");
        let block = serde_json::json!({
            "header": {"id": "blk1", "slot": 5, "parent_block": "blk0"},
            "transactions": [{
                "mantle_tx": {"hash": "tx1", "ops": [
                    {"opcode": 17, "payload": {"channel_id": channel_hex, "inscription": insc_hex}}
                ]}
            }]
        });
        let msgs = extract_block_messages(&block, &HashMap::new());
        assert_eq!(msgs.len(), 1);
        assert_eq!(msgs[0].sender, "alice");
        assert_eq!(msgs[0].text, "hi #live");
        assert!(msgs[0].live);
    }

    #[test]
    fn extract_skips_lez_system_inscriptions() {
        let insc_hex = hex::encode("/LEZ/ClockProgramAccount/0000001");
        let block = serde_json::json!({
            "header": {"id": "blk1", "slot": 5, "parent_block": "blk0"},
            "transactions": [{
                "mantle_tx": {"hash": "tx1", "ops": [
                    {"opcode": 17, "payload": {"channel_id": "0101010101010101010101010101010101010101010101010101010101010101", "inscription": insc_hex}}
                ]}
            }]
        });
        assert!(extract_block_messages(&block, &HashMap::new()).is_empty());
    }

    #[test]
    fn preview_text_does_not_slice_inside_multibyte_codepoint() {
        let text = "🚀".repeat(90);

        assert_eq!(preview_text(&text, 60).chars().count(), 60);
    }

    #[test]
    fn decode_yolo_channel_fallback_does_not_slice_inside_multibyte_input() {
        let text = decode_yolo_channel("🚀🚀🚀🚀🚀🚀🚀🚀");

        assert_eq!(text.chars().count(), 8);
    }
}
