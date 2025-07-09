#!/bin/sh
# Optional browser test; requires agent-browser 0.38.1. Start the docs preview first.
set -eu
cd "$(dirname "$0")/.."
url="${1:-http://127.0.0.1:8008}"
agent-browser --session portfolio-smoke open "$url"
agent-browser --session portfolio-smoke wait --fn "!document.querySelector('#status').textContent.includes('Loading')"
agent-browser --session portfolio-smoke eval --stdin < tests/pages-smoke.js
