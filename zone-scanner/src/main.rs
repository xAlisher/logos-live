/// Zone-board block scanner
///
/// Walks the Logos blockchain backward from the current tip, extracts
/// opcode=17 inscriptions on `logos:yolo:*` channels, and accumulates
/// them in zone_scan.json.  Runs continuously: after finishing a full
/// backward pass it watches the tip for new blocks.
use std::{collections::HashSet, time::Duration};

use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::time::sleep;

// ── Config ────────────────────────────────────────────────────────────────────
const NODE_URL: &str = "http://127.0.0.1:8080";
/// "logos:yolo:" as hex prefix (channel IDs are 32-byte zero-padded)
const YOLO_HEX: &str = "6c6f676f733a796f6c6f3a";
/// How many slots to request per batch from /cryptarchia/blocks
const BATCH_SLOTS: u64 = 2_000;
/// Slots to poll when watching the tip for new blocks
const POLL_INTERVAL_SECS: u64 = 30;
/// Output file (relative to cwd, override via ZONE_SCAN_FILE env var)
fn out_file() -> String {
    std::env::var("ZONE_SCAN_FILE").unwrap_or_else(|_| "../zone_scan.json".into())
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

#[derive(Serialize, Deserialize, Default)]
struct ScanDb {
    /// Messages found, sorted by slot ascending
    messages: Vec<ZoneMessage>,
    /// Block hashes we have already processed (to avoid re-fetching)
    seen_blocks: HashSet<String>,
    /// Lowest slot we have scanned down to (for backward walk)
    scanned_to: u64,
    /// Highest slot we have seen (for forward watch)
    scanned_tip: u64,
}

// ── Main ──────────────────────────────────────────────────────────────────────
#[tokio::main]
async fn main() -> Result<()> {
    let path = out_file();
    eprintln!("Zone scanner starting. Output: {path}");

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()?;

    let mut db = load_db(&path);

    // ── Phase 1: backward scan from tip to genesis ────────────────────────────
    let tip_slot = get_tip_slot(&client).await?;
    eprintln!("Current tip slot: {tip_slot}");

    // Start from where we left off (or tip if first run)
    let scan_start = resume_scan_start(&db, tip_slot);
    db.scanned_tip = tip_slot;

    let mut slot_hi = scan_start;
    loop {
        let slot_lo = slot_hi.saturating_sub(BATCH_SLOTS);
        eprintln!("  Scanning slots {slot_lo}–{slot_hi} …");

        let new_msgs = match get_zone_messages_in_range(
            &client,
            slot_lo,
            slot_hi,
            &mut db.seen_blocks,
        )
        .await
        {
            Ok(messages) => messages,
            Err(error) => {
                eprintln!("    Blocks error: {error}; retrying this slot range later");
                sleep(Duration::from_secs(5)).await;
                continue;
            }
        };

        if !new_msgs.is_empty() {
            eprintln!("    +{} zone messages found", new_msgs.len());
            for m in &new_msgs {
                eprintln!(
                    "      [{slot}] {sender}: {text}",
                    slot = m.slot,
                    sender = m.sender,
                    text = preview_text(&m.text, 60)
                );
            }
            db.messages.extend(new_msgs);
            dedup_messages(&mut db.messages);
        }

        db.scanned_to = slot_lo;
        save_db(&path, &db);

        if slot_lo == 0 {
            eprintln!(
                "Backward scan complete. {} total messages.",
                db.messages.len()
            );
            break;
        }
        slot_hi = slot_lo;
    }

    // ── Phase 2: watch tip for new blocks ─────────────────────────────────────
    eprintln!("Watching tip for new messages…");
    loop {
        sleep(Duration::from_secs(POLL_INTERVAL_SECS)).await;

        let new_tip = match get_tip_slot(&client).await {
            Ok(s) => s,
            Err(e) => {
                eprintln!("Tip error: {e}");
                continue;
            }
        };

        if new_tip <= db.scanned_tip {
            continue;
        }

        let new_msgs =
            match get_zone_messages_in_range(&client, db.scanned_tip, new_tip, &mut db.seen_blocks)
                .await
            {
                Ok(m) => m,
                Err(e) => {
                    eprintln!("Blocks error: {e}");
                    continue;
                }
            };
        if !new_msgs.is_empty() {
            eprintln!("+{} new zone messages at tip", new_msgs.len());
            for m in &new_msgs {
                eprintln!(
                    "  [{slot}] {sender}: {text}",
                    slot = m.slot,
                    sender = m.sender,
                    text = preview_text(&m.text, 80)
                );
            }
            db.messages.extend(new_msgs);
            dedup_messages(&mut db.messages);
        }

        db.scanned_tip = new_tip;
        save_db(&path, &db);
    }
}

// ── Node API helpers ──────────────────────────────────────────────────────────
async fn get_tip_slot(client: &reqwest::Client) -> Result<u64> {
    #[derive(Deserialize)]
    struct Info {
        slot: u64,
    }
    let info: Info = client
        .get(format!("{NODE_URL}/cryptarchia/info"))
        .send()
        .await?
        .json()
        .await?;
    Ok(info.slot)
}

/// Fetch a slot range and return all zone messages found inline.
/// /cryptarchia/blocks returns full block objects — no secondary fetch needed.
async fn get_zone_messages_in_range(
    client: &reqwest::Client,
    slot_from: u64,
    slot_to: u64,
    seen_blocks: &mut HashSet<String>,
) -> Result<Vec<ZoneMessage>> {
    let url = format!("{NODE_URL}/cryptarchia/blocks?slot_from={slot_from}&slot_to={slot_to}");
    let blocks: Vec<serde_json::Value> = client.get(&url).send().await?.json().await?;

    let mut msgs = Vec::new();
    for block in &blocks {
        let block_id = block["header"]["id"].as_str().unwrap_or("").to_string();
        if seen_blocks.contains(&block_id) {
            continue;
        }
        seen_blocks.insert(block_id.clone());

        let slot = block["header"]["slot"].as_u64().unwrap_or(0);

        let txs = match block["transactions"].as_array() {
            Some(t) => t,
            None => continue,
        };
        for tx in txs {
            let tx_id = tx["mantle_tx"]["hash"].as_str().unwrap_or("").to_string();
            let ops = match tx["mantle_tx"]["ops"].as_array() {
                Some(o) => o,
                None => continue,
            };
            for op in ops {
                if op["opcode"].as_u64() != Some(17) {
                    continue;
                }
                let ch = op["payload"]["channel_id"].as_str().unwrap_or("");
                if !ch.starts_with(YOLO_HEX) {
                    continue;
                }

                let sender = decode_yolo_channel(ch);

                let raw: Option<Vec<u8>> = op["payload"]["inscription"].as_array().map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_u64().map(|b| b as u8))
                        .collect()
                });
                let text = match raw.as_ref().and_then(|b| std::str::from_utf8(b).ok()) {
                    Some(s) => s.trim().to_string(),
                    None => continue,
                };
                if text.is_empty() {
                    continue;
                }
                if text.starts_with('{') && text.contains("\"type\"") {
                    continue;
                }

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
    }
    Ok(msgs)
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

fn resume_scan_start(db: &ScanDb, tip_slot: u64) -> u64 {
    if db.scanned_to > 0 {
        db.scanned_to
    } else if db.scanned_tip > 0 {
        db.scanned_tip
    } else {
        tip_slot
    }
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
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_db(path: &str, db: &ScanDb) {
    if let Ok(json) = serde_json::to_string_pretty(db) {
        let _ = std::fs::write(path, json);
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
    fn resume_scan_start_prefers_scanned_to_for_interrupted_backward_scan() {
        let db = ScanDb {
            scanned_to: 5_000,
            scanned_tip: 10_000,
            ..ScanDb::default()
        };

        assert_eq!(resume_scan_start(&db, 12_000), 5_000);
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
