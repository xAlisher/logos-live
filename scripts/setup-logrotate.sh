#!/usr/bin/env bash
# setup-logrotate.sh
# Installs the logrotate config for logos-node-visualizer logs.
# Run once on a new machine after cloning.
#
# Usage:
#   bash scripts/setup-logrotate.sh
#   VISUALIZER_PATH=/custom/path bash scripts/setup-logrotate.sh

set -e

VISUALIZER="${VISUALIZER_PATH:-$HOME/logos-node-visualizer}"
RUNBOOK="${RUNBOOK_PATH:-$HOME/logos-blockchain-runbook}"
CONFIG_DIR="$HOME/.config/logrotate"

mkdir -p "$CONFIG_DIR"

cat > "$CONFIG_DIR/logos-visualizer" <<EOF
$VISUALIZER/crawler.log
$VISUALIZER/zone-scanner.log
$RUNBOOK/state/zone-board-v0.2.2/zone-board.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    copytruncate
}
EOF

echo "Installed logrotate config to $CONFIG_DIR/logos-visualizer"
echo "Test with: logrotate -d $CONFIG_DIR/logos-visualizer"
echo "Force run: logrotate -f $CONFIG_DIR/logos-visualizer"
