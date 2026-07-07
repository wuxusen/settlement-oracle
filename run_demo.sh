#!/usr/bin/env bash
# One command: settle a real World Cup fixture, chain + (optionally) anchor the
# result, then independently verify it — and show a tampered result being
# rejected. Runs fully offline against the committed real results.
#
#   ./run_demo.sh                 # Portugal v Croatia (2-1, has a real VAR overturn)
#   ./run_demo.sh 18176123        # Australia v Egypt (1-1 draw)
#   ./run_demo.sh 18179763 --anchor   # also anchor on Solana devnet (needs a wallet)
set -euo pipefail
cd "$(dirname "$0")"

FIXTURE="${1:-18179763}"
shift || true

exec python3 -m settlement_oracle.cli demo --fixture "$FIXTURE" "$@"
