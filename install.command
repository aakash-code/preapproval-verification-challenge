#!/usr/bin/env bash
# Double-click this file in Finder to set up and (optionally) start the app.
# It just runs scripts/install.sh — see that file for what actually happens.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
./scripts/install.sh
echo ""
read -r -p "Press Enter to close this window..." _ || true
