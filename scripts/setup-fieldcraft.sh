#!/usr/bin/env bash
# setup-fieldcraft.sh
# Creates .claude/rules/ symlinks pointing at the local fieldcraft clone.
# Run once after cloning a fieldcraft-retrofitted project.
#
# Usage:
#   bash scripts/setup-fieldcraft.sh
#   FIELDCRAFT_PATH=/custom/path bash scripts/setup-fieldcraft.sh

set -e

FC="${FIELDCRAFT_PATH:-$HOME/fieldcraft}"

if [ ! -d "$FC" ]; then
  echo "ERROR: fieldcraft not found at $FC"
  echo "Clone it there, or set FIELDCRAFT_PATH to its location."
  exit 1
fi

mkdir -p .claude/rules

link() {
  local name="$1" rel_path="$2"
  ln -sf "$FC/$rel_path" ".claude/rules/$name.md"
  echo "  linked $name.md -> $FC/$rel_path"
}

link fergie        "agents/fergie.md"
link session-start "protocols/session-start.md"
link builder-auditor "protocols/builder-auditor.md"
link halt-resume   "protocols/halt-resume.md"

echo "Done. .claude/rules/ linked to $FC"
