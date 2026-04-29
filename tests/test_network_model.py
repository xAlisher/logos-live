import asyncio
import copy
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from fastapi.testclient import TestClient

from server import (
    build_network_summary,
    build_agent_manifest,
    build_node_verification,
    build_node_visibility,
    build_telemetry_snapshot,
    classify_node_environment,
    hydrate_content_snapshot,
    logos_node_setup_skill,
    merge_peer_snapshot,
    parse_peer_log_observations,
    parse_stake_log_points,
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


def test_merge_peer_snapshot_uses_published_peers_when_local_node_is_unavailable():
    local = {
        "chain": {},
        "network": {},
        "nodes": [
            {"peer_id": "", "ip": "45.65.190.91", "self": True, "online": True}
        ],
        "updated": 111,
        "total_peers_in_logs": 0,
    }
    fallback = {
        "nodes": [
            {"peer_id": "published-a", "ip": "65.109.51.37", "country": "Finland", "online": True},
            {"peer_id": "published-b", "ip": "1.2.3.4", "country": "Costa Rica", "online": True},
        ],
        "updated": 99,
    }

    merged = merge_peer_snapshot(copy.deepcopy(local), fallback)

    assert [node["peer_id"] for node in merged["nodes"]] == ["", "published-a", "published-b"]
    assert merged["peer_data_source"] == "published-fallback"
    assert merged["peer_snapshot"]["nodes"] == 2


def test_hydrate_content_snapshot_restores_public_map_content_when_local_lacks_it():
    local = {
        "nodes": [],
        "chain": {},
        "network": {},
        "updated": 123,
    }
    published = {
        "events": [{"name": "Logos Circle", "lat": 1, "lon": 2}],
        "youtube": {"video_id": "abc123", "title": "Latest Logos update"},
        "zone_messages": [{"sender": "alice", "text": "online #live"}],
        "github": [{"title": "commit"}],
        "discourse": [{"title": "topic"}],
        "stake": {"recipients": 10},
    }

    hydrated = hydrate_content_snapshot(copy.deepcopy(local), published)

    assert hydrated["events"] == published["events"]
    assert hydrated["youtube"] == published["youtube"]
    assert hydrated["zone_messages"] == published["zone_messages"]
    assert hydrated["github"] == published["github"]
    assert hydrated["discourse"] == published["discourse"]
    assert hydrated["stake"] == published["stake"]
    assert hydrated["content_data_source"] == "published-fallback"


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


def test_parse_peer_log_observations_buckets_peer_mentions_from_logs():
    text = "\n".join(
        [
            "2026-04-28T22:03:10Z connected 12D3KooW11111111111111111111111111111111111111111111",
            "2026-04-28T22:44:10Z identify 12D3KooW11111111111111111111111111111111111111111111",
            "2026-04-28 23:02:00 added 12D3KooW22222222222222222222222222222222222222222222",
        ]
    )

    observations = parse_peer_log_observations(text)

    assert observations == [
        {"peer_id": "12D3KooW11111111111111111111111111111111111111111111", "ts": 1777413790},
        {"peer_id": "12D3KooW11111111111111111111111111111111111111111111", "ts": 1777416250},
        {"peer_id": "12D3KooW22222222222222222222222222222222222222222222", "ts": 1777417320},
    ]


def test_parse_stake_log_points_extracts_total_stake_events():
    text = "\n".join(
        [
            "2026-04-28T22:00:00Z tsi_update old_total_stake=400000",
            "2026-04-28T23:00:00Z tsi_update total_stake=1200000 peer=Psiyol",
        ]
    )

    points = parse_stake_log_points(text)

    assert points == [
        {"ts": 1777413600, "value": 400000.0, "source": "log"},
        {"ts": 1777417200, "value": 1200000.0, "source": "log"},
    ]


def test_build_telemetry_snapshot_summarizes_hourly_activity_uptime_and_stake():
    nodes = [
        {
            "peer_id": "peer-a",
            "first_seen": 1777410000,
            "last_seen": 1777417200,
            "online": True,
        },
        {
            "peer_id": "peer-b",
            "first_seen": 1777413600,
            "last_seen": 1777417200,
            "online": True,
        },
    ]
    observations = [
        {"peer_id": "peer-a", "ts": 1777410200},
        {"peer_id": "peer-a", "ts": 1777410500},
        {"peer_id": "peer-b", "ts": 1777410500},
        {"peer_id": "peer-a", "ts": 1777417500},
    ]
    stake_points = [
        {"ts": 1777410000, "value": 400000},
        {"ts": 1777417200, "value": 1200000},
    ]

    telemetry = build_telemetry_snapshot(
        nodes=nodes,
        observations=observations,
        stake_points=stake_points,
        now=1777417200,
        window_hours=4,
    )

    assert telemetry["summary"]["active_peak"] == 2
    assert telemetry["summary"]["tracked_peers"] == 2
    assert telemetry["summary"]["latest_total_stake"] == 1200000
    assert telemetry["active_peers_hourly"][-1]["count"] == 1
    assert telemetry["peer_uptime"][0]["peer_id"] == "peer-a"
    assert telemetry["peer_uptime"][0]["active_hours"] == 2
    assert telemetry["stake_estimate_hourly"][-1]["value"] == 1200000
    assert telemetry["annotations"][0]["kind"] == "stake-spike"


def test_build_telemetry_snapshot_warns_when_uptime_is_synthesized_from_crawler_seen_range():
    telemetry = build_telemetry_snapshot(
        nodes=[
            {
                "peer_id": "peer-a",
                "first_seen": 1777410000,
                "last_seen": 1777417200,
                "online": True,
            }
        ],
        observations=[],
        now=1777417200,
        window_hours=4,
    )

    assert telemetry["source"] == "crawler-first-last-seen"
    assert any("synthesized" in warning for warning in telemetry["warnings"])


def test_agent_manifest_exposes_setup_verification_and_release_contracts():
    manifest = build_agent_manifest("https://logos-live.example")

    assert manifest["skill_version"] == "1.0.0"
    assert manifest["release"]["node_version"] == "0.1.2"
    assert manifest["release"]["circuits_version"] == "0.4.2"
    assert manifest["capabilities"] == [
        "network_inspection",
        "telemetry",
        "node_setup_guidance",
        "node_visibility_verification",
    ]
    assert manifest["endpoints"]["setup_skill"]["url"] == "https://logos-live.example/agents/logos-node-setup-skill.md"
    assert manifest["endpoints"]["verify_node"]["url_template"] == "https://logos-live.example/api/agent/verify-node/{peer_id}"
    assert len(manifest["bootstrap_peers"]) == 4
    assert manifest["bootstrap_peers"][0]["multiaddr"].startswith("/ip4/65.109.51.37/udp/3000")
    assert "v0.4.2-linux-x86_64.tar.gz" in manifest["release"]["assets"]["circuits"]["linux-x86_64"]
    assert "v0.4.1" not in str(manifest)


def test_node_setup_skill_contains_verified_assets_bootstrap_peers_and_verification_loop():
    skill = logos_node_setup_skill("https://logos-live.example")

    assert "logos-blockchain-node-linux-x86_64-0.1.2.tar.gz" in skill
    assert "logos-blockchain-circuits-v0.4.2-linux-x86_64.tar.gz" in skill
    assert "logos-blockchain-node-macos-aarch64-0.1.2.tar.gz" in skill
    assert "uname -s" in skill
    assert "case \"$(uname -s)-$(uname -m)\"" in skill
    assert "chmod +x ./logos-blockchain-node" in skill
    assert "logos-blockchain-circuits-v0.4.1" not in skill
    assert skill.count("-p /ip4/65.109.51.37/udp/") == 4
    assert "curl -s http://localhost:8080/cryptarchia/info" in skill
    assert "grep -A3 known_keys user_config.yaml" in skill
    assert "https://devnet.blockchain.logos.co/web/faucet/" in skill
    assert "curl -s http://localhost:8080/wallet/<public-key>/balance" in skill
    assert "https://logos-live.example/api/agent/verify-node/{peer_id}" in skill
    assert "xattr -dr com.apple.quarantine ~/.logos-blockchain-circuits" in skill


def test_build_node_visibility_reports_map_visibility_and_missing_peer_actions():
    data = {
        "nodes": [
            {
                "peer_id": "peer-visible",
                "ip": "1.2.3.4",
                "country": "Costa Rica",
                "city": "San Jose",
                "online": True,
                "last_seen": 1777417200,
            }
        ],
        "telemetry": {
            "peer_uptime": [
                {"peer_id": "peer-visible", "active_hours": 3, "uptime_pct": 75.0}
            ]
        },
    }

    visible = build_node_visibility("peer-visible", data)
    missing = build_node_visibility("peer-missing", data)

    assert visible["visible_on_map"] is True
    assert visible["observed_in_telemetry"] is True
    assert visible["location"] == "San Jose, Costa Rica"
    assert missing["visible_on_map"] is False
    assert missing["next_actions"][0].startswith("Wait for the crawler")


def test_build_node_verification_reports_setup_stages_for_local_and_remote_peers():
    data = {
        "chain": {"mode": "Online", "height": 100, "slot": 500},
        "network": {"peer_id": "peer-local", "n_peers": 8, "n_connections": 10},
        "nodes": [
            {
                "peer_id": "peer-local",
                "ip": "1.2.3.4",
                "city": "San Jose",
                "country": "Costa Rica",
                "online": True,
                "last_seen": 1777417200,
            }
        ],
        "telemetry": {"peer_uptime": [{"peer_id": "peer-local", "active_hours": 2, "uptime_pct": 50.0}]},
    }

    verification = build_node_verification("peer-local", data)
    stages = {stage["id"]: stage["status"] for stage in verification["stages"]}

    assert verification["overall_status"] == "ready"
    assert stages["node_api"] == "passed"
    assert stages["consensus_online"] == "passed"
    assert stages["peer_connectivity"] == "passed"
    assert stages["crawler_observed"] == "passed"
    assert stages["map_visible"] == "passed"
    assert verification["next_actions"] == []


def test_build_node_verification_is_ready_before_crawler_visibility_for_healthy_local_node():
    data = {
        "chain": {"mode": "Online", "height": 100, "slot": 500},
        "network": {"peer_id": "peer-local", "n_peers": 8, "n_connections": 10},
        "nodes": [],
        "telemetry": {"peer_uptime": []},
    }

    verification = build_node_verification("peer-local", data)
    stages = {stage["id"]: stage["status"] for stage in verification["stages"]}

    assert verification["overall_status"] == "ready"
    assert stages["node_api"] == "passed"
    assert stages["consensus_online"] == "passed"
    assert stages["peer_connectivity"] == "passed"
    assert stages["crawler_observed"] == "warning"
    assert stages["map_visible"] == "warning"
    assert verification["next_actions"]


def test_invalid_peer_id_path_params_return_400():
    client = TestClient(server.app)

    assert client.get("/api/agent/verify-node/not-a-peer").status_code == 400
    assert client.get("/api/agent/node-visibility/not-a-peer").status_code == 400


def test_git_snapshot_fallback_is_opt_in(monkeypatch):
    monkeypatch.delenv("ENABLE_GIT_SNAPSHOT_FALLBACK", raising=False)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("git fallback should not run unless explicitly enabled")

    monkeypatch.setattr(server.subprocess, "run", fail_if_called)

    assert server._load_published_snapshot_from_git() is None


def test_git_snapshot_fallback_reads_snapshot_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_GIT_SNAPSHOT_FALLBACK", "1")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='{"nodes": [{"peer_id": "peer-a"}]}')

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    assert server._load_published_snapshot_from_git()["nodes"][0]["peer_id"] == "peer-a"


def test_api_network_serializes_stale_cache_rebuilds(monkeypatch):
    calls = 0

    async def fake_build_data():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"updated": calls}

    async def run_requests():
        return await asyncio.gather(*(server.api_network() for _ in range(8)))

    monkeypatch.setattr(server, "_build_data", fake_build_data)
    monkeypatch.setattr(server, "_cache", None)
    monkeypatch.setattr(server, "_cache_ts", 0.0)
    monkeypatch.setattr(server, "CACHE_TTL", 60)

    results = asyncio.run(run_requests())

    assert calls == 1
    assert all(result == {"updated": 1} for result in results)


def test_is_public_rejects_ipv6_and_malformed_addresses():
    assert server._is_public("::1") is False
    assert server._is_public("not-an-ip") is False
    assert server._is_public("999.1.1.1") is False


def test_collect_telemetry_from_logs_writes_agent_readable_snapshot(tmp_path):
    from telemetry_collector import collect_telemetry_from_logs

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "logos-blockchain.001").write_text(
        "\n".join(
            [
                "2026-04-28T22:03:10Z connected 12D3KooW11111111111111111111111111111111111111111111",
                "2026-04-28T23:00:00Z tsi_update total_stake=1200000",
            ]
        )
    )
    output = tmp_path / "telemetry.json"

    snapshot = collect_telemetry_from_logs(log_dir, output, now=1777417200, window_hours=4)

    assert output.exists()
    assert snapshot["source"] == "logs"
    assert snapshot["summary"]["tracked_peers"] == 1
    assert snapshot["summary"]["latest_total_stake"] == 1200000
    assert snapshot["observations"][0]["peer_id"].startswith("12D3KooW")
    assert snapshot["stake_points"][0]["value"] == 1200000


def test_collect_telemetry_from_logs_defaults_window_to_latest_log_timestamp(tmp_path):
    from telemetry_collector import collect_telemetry_from_logs

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "logos-blockchain.001").write_text(
        "2026-04-28T22:03:10Z connected 12D3KooW11111111111111111111111111111111111111111111\n"
    )

    snapshot = collect_telemetry_from_logs(log_dir, tmp_path / "telemetry.json", window_hours=4)

    assert snapshot["summary"]["tracked_peers"] == 1
