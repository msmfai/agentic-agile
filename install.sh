#!/bin/sh
# Bootstrap Agentic Agile: fetch a checkout, then hand over to `agentic-agile install`.
#
#   sh install.sh                       # every agent found on PATH, global, full
#   sh install.sh claude --variant lite
#   sh install.sh self --scope project  # run by an agent, for the agent
#
# Everything after the script name goes to `agentic-agile install` unchanged.
# AGENTIC_AGILE_HOME sets where the checkout lives (default ~/.local/share/agentic-agile).
set -eu

repo="${AGENTIC_AGILE_REPO:-https://github.com/msmfai/agentic-agile}"
home="${AGENTIC_AGILE_HOME:-$HOME/.local/share/agentic-agile}"

command -v git >/dev/null 2>&1 || { echo "agentic-agile: git is required" >&2; exit 1; }

if [ -d "$home/.git" ]; then
  git -C "$home" pull --ff-only --quiet
else
  git clone --quiet "$repo" "$home"
fi

[ "$#" -gt 0 ] || set -- all
exec "$home/bin/agentic-agile" install "$@"
