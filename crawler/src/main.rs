/// Logos network crawler
///
/// Dials bootstrap peers, runs Kademlia FIND_NODE queries across the keyspace,
/// collects all discovered peers + their multiaddrs, and writes peers.json.
/// Repeats every CRAWL_INTERVAL_SECS seconds.
use std::{
    collections::HashMap,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use anyhow::Result;
use futures::StreamExt;
use libp2p::{
    identify, kad,
    multiaddr::Protocol,
    swarm::{NetworkBehaviour, SwarmEvent},
    Multiaddr, PeerId, StreamProtocol,
};
use rand::Rng;
use serde::{Deserialize, Serialize};

// ── Bootstrap peers ──────────────────────────────────────────────────────────
// Source of truth for the bootstrap set (server.py mirrors these — keep in sync).
// Verified live against the v0.2 node's /network/info `connected_peers` on
// 2026-07-04: all four IDs are in the live peer set and the crawler dials them
// successfully. Addresses unchanged across the v0.1.2→v0.2 fork.
const BOOTSTRAP: &[(&str, &str)] = &[
    (
        "12D3KooWFrouXfmrR4nsLMtE7wu15DoMJ6VtoUtHinREZCvbWHar",
        "/ip4/65.109.51.37/udp/3000/quic-v1",
    ),
    (
        "12D3KooWJRGau8M1rjT7R5e4YYsgdFhsMX35nRDtMwCDjxQkXAHz",
        "/ip4/65.109.51.37/udp/3001/quic-v1",
    ),
    (
        "12D3KooWQXJavMDTRscjauFSgVAB1VLB6Rzpy2uY5SU9Tk7927tb",
        "/ip4/65.109.51.37/udp/3002/quic-v1",
    ),
    (
        "12D3KooWSQc7CcGtvWDPF1yCbBthFnQjprfCVHmfmNDUrSmqQsU1",
        "/ip4/65.109.51.37/udp/3003/quic-v1",
    ),
];

// Kademlia protocol name. VERIFIED unchanged across the v0.1.2→v0.2 fork
// (2026-07-04): the running crawler uses this compiled default with no
// KAD_PROTOCOL override and still discovers live v0.2 peers, so the v0.2 DHT
// kept the `/logos-blockchain-testnet-v0.1.2/kad/1.0.0` id. Do not "modernize"
// this string to v0.2 — that would break discovery. Override via env if the
// network ever changes it: KAD_PROTOCOL=/my/kad/1.0.0
fn kad_protocol() -> StreamProtocol {
    let s = std::env::var("KAD_PROTOCOL")
        .unwrap_or_else(|_| "/logos-blockchain-testnet-v0.1.2/kad/1.0.0".to_string());
    StreamProtocol::try_from_owned(s).expect("invalid KAD_PROTOCOL")
}

const CRAWL_SECS: u64         = 60;   // how long each crawl collects events
const IDLE_TIMEOUT_SECS: u64  = 30;   // connection idle timeout
const CRAWL_INTERVAL_SECS: u64 = 600; // sleep between crawls
const NUM_QUERIES: usize       = 20;   // random FIND_NODE queries per crawl

// ── Behaviour ─────────────────────────────────────────────────────────────────
#[derive(NetworkBehaviour)]
struct Behaviour {
    kad:      kad::Behaviour<kad::store::MemoryStore>,
    identify: identify::Behaviour,
}

// ── Persistent node DB ────────────────────────────────────────────────────────
#[derive(Serialize, Deserialize, Clone)]
struct NodeRecord {
    peer_id:    String,
    addrs:      Vec<String>,
    first_seen: u64,
    last_seen:  u64,
}

#[derive(Serialize, Deserialize, Default)]
struct NodeDb {
    nodes:      HashMap<String, NodeRecord>,
    last_crawl: u64,
}

// ── Main ──────────────────────────────────────────────────────────────────────
#[tokio::main]
async fn main() -> Result<()> {
    let output = std::env::var("PEERS_FILE")
        .unwrap_or_else(|_| "../peers.json".to_string());

    eprintln!("Logos crawler starting. Output: {output}");
    eprintln!("Kademlia protocol: {}", kad_protocol());

    loop {
        match crawl(&output).await {
            Ok(n)  => eprintln!("Crawl complete — {n} peers written to {output}"),
            Err(e) => eprintln!("Crawl error: {e}"),
        }
        eprintln!("Sleeping {}s until next crawl…", CRAWL_INTERVAL_SECS);
        tokio::time::sleep(Duration::from_secs(CRAWL_INTERVAL_SECS)).await;
    }
}

// ── Crawl ─────────────────────────────────────────────────────────────────────
async fn crawl(output_path: &str) -> Result<usize> {
    let keypair    = libp2p::identity::Keypair::generate_ed25519();
    let local_peer = PeerId::from(keypair.public());

    let mut kad_cfg = kad::Config::new(kad_protocol());
    kad_cfg.set_query_timeout(Duration::from_secs(CRAWL_SECS / 2));

    let mut swarm = libp2p::SwarmBuilder::with_existing_identity(keypair)
        .with_tokio()
        .with_quic()
        .with_behaviour(|key| {
            let store = kad::store::MemoryStore::new(local_peer);
            let kad   = kad::Behaviour::with_config(local_peer, store, kad_cfg);
            let id    = identify::Behaviour::new(identify::Config::new(
                "/logos-crawler/0.1.0".to_string(),
                key.public(),
            ));
            Behaviour { kad, identify: id }
        })?
        .with_swarm_config(|c| {
            c.with_idle_connection_timeout(Duration::from_secs(IDLE_TIMEOUT_SECS))
        })
        .build();

    // Register + dial bootstrap peers
    for (peer_str, addr_str) in BOOTSTRAP {
        let peer: PeerId    = peer_str.parse()?;
        let addr: Multiaddr = addr_str.parse()?;
        swarm.behaviour_mut().kad.add_address(&peer, addr.clone());
        let _ = swarm.dial(addr);
    }

    // Bootstrap DHT then issue random queries to walk the keyspace
    let _ = swarm.behaviour_mut().kad.bootstrap();
    let mut rng = rand::thread_rng();
    for _ in 0..NUM_QUERIES {
        let key: [u8; 32] = rng.gen();
        swarm.behaviour_mut().kad.get_closest_peers(key.to_vec());
    }

    // Collect events for CRAWL_SECS
    let mut discovered: HashMap<PeerId, Vec<Multiaddr>> = HashMap::new();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(CRAWL_SECS);

    loop {
        tokio::select! {
            event = swarm.select_next_some() => {
                handle_event(event, local_peer, &mut discovered);
            }
            _ = tokio::time::sleep_until(deadline) => break,
        }
    }

    update_db(output_path, discovered)
}

fn handle_event(
    event: SwarmEvent<BehaviourEvent>,
    local_peer: PeerId,
    discovered: &mut HashMap<PeerId, Vec<Multiaddr>>,
) {
    match event {
        // Capture peer + address directly from every established connection
        SwarmEvent::ConnectionEstablished { peer_id, endpoint, .. } => {
            if peer_id != local_peer {
                let addr = endpoint.get_remote_address().clone();
                discovered.entry(peer_id).or_default().push(addr);
            }
        }

        // Peers returned by FIND_NODE responses
        SwarmEvent::Behaviour(BehaviourEvent::Kad(
            kad::Event::OutboundQueryProgressed {
                result: kad::QueryResult::GetClosestPeers(result),
                ..
            },
        )) => {
            let peers = match result {
                Ok(ok)  => ok.peers,
                Err(kad::GetClosestPeersError::Timeout { peers, .. }) => peers,
            };
            for info in peers {
                if info.peer_id != local_peer {
                    discovered
                        .entry(info.peer_id)
                        .or_default()
                        .extend(info.addrs);
                }
            }
        }

        // Routing table updates carry the peer's known addresses
        SwarmEvent::Behaviour(BehaviourEvent::Kad(kad::Event::RoutingUpdated {
            peer,
            addresses,
            ..
        })) => {
            if peer != local_peer {
                discovered
                    .entry(peer)
                    .or_default()
                    .extend(addresses.into_vec());
            }
        }

        // Identify gives us verified listen addresses + supported protocols
        SwarmEvent::Behaviour(BehaviourEvent::Identify(identify::Event::Received {
            peer_id,
            info,
            ..
        })) => {
            if peer_id != local_peer {
                // Log protocols so we can find the correct Kademlia protocol name
                for proto in &info.protocols {
                    if proto.as_ref().contains("kad") {
                        eprintln!("  [identify] peer {peer_id} supports kad protocol: {proto}");
                    }
                }
                discovered
                    .entry(peer_id)
                    .or_default()
                    .extend(info.listen_addrs);
            }
        }

        _ => {}
    }
}

fn is_public(addr: &Multiaddr) -> bool {
    for proto in addr.iter() {
        if let Protocol::Ip4(ip) = proto {
            let o = ip.octets();
            if o[0] == 127 { return false; }
            if o[0] == 10  { return false; }
            if o[0] == 172 && (16..=31).contains(&o[1]) { return false; }
            if o[0] == 192 && o[1] == 168 { return false; }
            if o[0] == 100 && (64..=127).contains(&o[1]) { return false; } // CGNAT
            if o[0] >= 224 { return false; } // multicast/reserved
            return true;
        }
    }
    false
}

fn update_db(path: &str, discovered: HashMap<PeerId, Vec<Multiaddr>>) -> Result<usize> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();

    // Load existing DB (or start fresh)
    let mut db: NodeDb = std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();

    db.last_crawl = now;

    for (peer_id, addrs) in discovered {
        let public: Vec<String> = addrs
            .iter()
            .filter(|a| is_public(a))
            .map(|a| a.to_string())
            .collect();
        if public.is_empty() {
            continue;
        }
        let key = peer_id.to_string();
        db.nodes
            .entry(key.clone())
            .and_modify(|r| {
                r.last_seen = now;
                // Merge any new addresses
                for a in &public {
                    if !r.addrs.contains(a) {
                        r.addrs.push(a.clone());
                    }
                }
            })
            .or_insert(NodeRecord {
                peer_id:    key,
                addrs:      public,
                first_seen: now,
                last_seen:  now,
            });
    }

    let online = db.nodes.values().filter(|r| r.last_seen == now).count();
    eprintln!("  DB: {} total nodes, {} online this crawl", db.nodes.len(), online);

    std::fs::write(path, serde_json::to_string_pretty(&db)?)?;
    Ok(online)
}
