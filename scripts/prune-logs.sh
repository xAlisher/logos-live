#!/usr/bin/env bash
# prune-logs.sh
# Compacts and prunes logos-node-visualizer logs via logrotate.
# Run on a schedule via cron:
#   0 2 * * * bash /home/alisher/logos-node-visualizer/scripts/prune-logs.sh

set -e

CONFIG="$HOME/.config/logrotate/logos-visualizer"
STATE="$HOME/.config/logrotate/state"

if [ ! -f "$CONFIG" ]; then
    echo "ERROR: logrotate config not found at $CONFIG" >&2
    exit 1
fi

logrotate --state "$STATE" "$CONFIG"
