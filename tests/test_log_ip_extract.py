import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish import _LOG_IP_A, _LOG_IP_B, _PRIVATE_IP


def test_pattern_a_matches_kbucket_add_line():
    line = (
        '2026-01-15T10:00:00 INFO Added address /ip4/1.2.3.4/tcp/4001 '
        'to peer PeerId("12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")'
    )
    matches = _LOG_IP_A.findall(line)
    assert matches == [("1.2.3.4", "12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")]


def test_pattern_b_matches_swarm_error_line():
    line = (
        '2026-01-15T10:00:01 ERROR dial failed /ip4/5.6.7.8/tcp/4001/p2p/'
        '12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde'
    )
    matches = _LOG_IP_B.findall(line)
    assert matches == [("5.6.7.8", "12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")]


def test_private_ip_filtered():
    private_ips = [
        "0.0.0.1",
        "10.0.0.1",
        "127.0.0.1",
        "172.16.0.1",
        "172.20.0.1",
        "172.31.255.255",
        "192.168.1.1",
    ]
    for ip in private_ips:
        assert _PRIVATE_IP.match(ip), f"{ip} should be private"

    public_ips = ["1.2.3.4", "8.8.8.8", "172.15.0.1", "172.32.0.1", "11.0.0.1"]
    for ip in public_ips:
        assert not _PRIVATE_IP.match(ip), f"{ip} should be public"


def test_dns4_address_captured():
    line = (
        '2026-01-15T10:00:02 INFO Added address /dns4/example.com/tcp/4001 '
        'to peer PeerId("12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")'
    )
    matches = _LOG_IP_A.findall(line)
    assert matches == [("example.com", "12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")]


def test_dedup_across_lines():
    lines = [
        '2026-01-15T10:00:00 INFO Added address /ip4/1.2.3.4/tcp/4001 to peer PeerId("12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")',
        '2026-01-15T10:01:00 INFO Added address /ip4/1.2.3.4/tcp/4001 to peer PeerId("12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde")',
        '2026-01-15T10:02:00 ERROR dial failed /ip4/1.2.3.4/tcp/4001/p2p/12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde',
    ]
    log_ip_peers: dict[str, set] = {}
    for line in lines:
        for ip, pid in _LOG_IP_A.findall(line):
            if not _PRIVATE_IP.match(ip):
                log_ip_peers.setdefault(ip, set()).add(pid)
        for ip, pid in _LOG_IP_B.findall(line):
            if not _PRIVATE_IP.match(ip):
                log_ip_peers.setdefault(ip, set()).add(pid)

    assert list(log_ip_peers.keys()) == ["1.2.3.4"]
    assert log_ip_peers["1.2.3.4"] == {"12D3KooWABCDEFGHJKMNPQRSTVWXYZabcde"}
