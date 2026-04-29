#!/usr/bin/env python3
"""Build telemetry.json from Logos node logs."""

import argparse
import json
import time
from pathlib import Path
from typing import Any

from server import _read_log_telemetry, build_telemetry_snapshot


def collect_telemetry_from_logs(
    log_dir: str | Path,
    output_path: str | Path,
    now: int | None = None,
    window_hours: int = 24 * 20,
) -> dict[str, Any]:
    observations, stake_points, log_files = _read_log_telemetry(str(log_dir))
    if now is None:
        timestamps = [
            int(point["ts"])
            for point in [*observations, *stake_points]
            if point.get("ts") is not None
        ]
        now = max(timestamps) if timestamps else int(time.time())
    now = int(now)
    snapshot = build_telemetry_snapshot(
        nodes=[],
        observations=observations,
        stake_points=stake_points,
        now=now,
        window_hours=window_hours,
        source="logs",
    )
    snapshot["source"] = "logs"
    snapshot["generated_at"] = now
    snapshot["observations"] = observations
    snapshot["stake_points"] = stake_points
    snapshot["log_files"] = log_files[-20:]

    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Logos Live telemetry.json from node logs.")
    parser.add_argument("--log-dir", required=True, help="Directory containing logos-blockchain.* log files.")
    parser.add_argument("--output", default="telemetry.json", help="Output telemetry JSON path.")
    parser.add_argument("--window-hours", type=int, default=24 * 20, help="Telemetry window to render.")
    args = parser.parse_args()

    snapshot = collect_telemetry_from_logs(args.log_dir, args.output, window_hours=args.window_hours)
    print(
        f"wrote {args.output}: "
        f"{snapshot['summary']['tracked_peers']} peers, "
        f"{snapshot['summary']['stake_points']} stake points"
    )


if __name__ == "__main__":
    main()
