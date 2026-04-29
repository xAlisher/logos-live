import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import (
    build_network_summary,
    classify_node_environment,
    merge_peer_snapshot,
    summarize_recent_blocks,
)


def test_classify_node_environment_marks_known_hosting_and_residential_networks():
    assert classify_node_environment(
        {
            "isp": "Hetzner Online GmbH",
            "org": "Hetzner",
            "asn": "AS24940 Hetzner Online GmbH",
        }
    ) == "Hosting"

    assert classify_node_environment(
        {
            "isp": "Telefonica de Espana SAU",
            "org": "RIMA (Red IP Multi Acceso)",
            "asn": "AS3352 TELEFONICA DE ESPANA S.A.U.",
        }
    ) == "Residential"


def test_build_network_summary_groups_countries_asns_and_infra_mix():
    nodes = [
        {
            "peer_id": "self",
            "ip": "1.1.1.1",
            "country": "Costa Rica",
            "city": "San Jose",
            "isp": "Comunicaciones Metropolitanas METROCOM, S.A.",
            "org": "Comunicaciones Metropolitanas METROCOM, S.A",
            "asn": "AS266853 Comunicaciones Metropolitanas METROCOM, S.A.",
            "self": True,
            "online": True,
        },
        {
            "peer_id": "hetzner-a",
            "ip": "65.109.51.37",
            "country": "Finland",
            "city": "Helsinki",
            "isp": "Hetzner Online GmbH",
            "org": "Hetzner Online GmbH",
            "asn": "AS24940 Hetzner Online GmbH",
            "online": True,
        },
        {
            "peer_id": "hetzner-b",
            "ip": "128.140.55.128",
            "country": "Germany",
            "city": "Falkenstein",
            "isp": "Hetzner Online GmbH",
            "org": "Hetzner",
            "asn": "AS24940 Hetzner Online GmbH",
            "online": False,
        },
    ]

    summary = build_network_summary(nodes, {"n_peers": 59, "n_connections": 60}, {"mode": "Online"})

    assert summary["nodes"]["total"] == 3
    assert summary["nodes"]["online"] == 2
    assert summary["nodes"]["countries"] == 3
    assert summary["geography"]["top_countries"][0]["country"] == "Costa Rica"
    assert summary["infrastructure"]["top_asns"][0]["asn"] == "AS24940 Hetzner Online GmbH"
    assert summary["infrastructure"]["mix"]["Hosting"] == 2
    assert summary["infrastructure"]["mix"]["Residential"] == 1
    assert summary["infrastructure"]["top_asn_share_pct"] == 66.67


def test_merge_peer_snapshot_uses_published_peers_when_local_has_only_self():
    local = {
        "chain": {"slot": 100, "mode": "Online"},
        "network": {"peer_id": "local-self", "n_peers": 59},
        "nodes": [
            {"peer_id": "local-self", "ip": "45.65.190.91", "self": True, "online": True}
        ],
        "updated": 111,
        "total_peers_in_logs": 0,
    }
    fallback = {
        "nodes": [
            {"peer_id": "old-self", "ip": "83.49.61.25", "self": True, "online": True},
            {"peer_id": "bootstrap", "ip": "65.109.51.37", "country": "Finland", "online": True},
        ],
        "updated": 99,
    }

    merged = merge_peer_snapshot(copy.deepcopy(local), fallback)

    assert merged["chain"] == local["chain"]
    assert merged["network"] == local["network"]
    assert [node["peer_id"] for node in merged["nodes"]] == ["local-self", "bootstrap"]
    assert merged["peer_data_source"] == "published-fallback"
    assert merged["peer_snapshot"]["updated"] == 99


def test_summarize_recent_blocks_counts_leaders_transactions_and_empty_slots():
    blocks = [
        {
            "header": {
                "slot": 10,
                "block_root": "root-a",
                "proof_of_leadership": {"leader_key": "leader-a"},
            },
            "transactions": [{"mantle_tx": {"ops": [{"opcode": 17}]}}],
        },
        {
            "header": {
                "slot": 8,
                "block_root": "root-b",
                "proof_of_leadership": {"leader_key": "leader-a"},
            },
            "transactions": [],
        },
        {
            "header": {
                "slot": 7,
                "block_root": "root-c",
                "proof_of_leadership": {"leader_key": "leader-b"},
            },
            "transactions": [{"mantle_tx": {"ops": [{"opcode": 0}, {"opcode": 17}]}}],
        },
    ]

    summary = summarize_recent_blocks(blocks)

    assert summary["window"]["blocks"] == 3
    assert summary["window"]["slot_span"] == 4
    assert summary["window"]["missed_or_unseen_slots"] == 1
    assert summary["window"]["empty_blocks"] == 1
    assert summary["window"]["transactions"] == 2
    assert summary["window"]["operations"] == 3
    assert summary["leader_diversity"]["unique_leaders"] == 2
    assert summary["leader_diversity"]["top_leader_share_pct"] == 66.67
    assert summary["top_leaders"][0]["leader_key"] == "leader-a"
