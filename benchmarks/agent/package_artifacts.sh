#!/bin/bash
# Compress the bulky traces, leave everything a reader needs in plain text, and checksum
# the lot. Traces are evidence to be re-read on demand; manifests, summaries, patches and
# configs are read directly, so they stay uncompressed.
set -euo pipefail
cd "$(dirname "$0")"

for f in run-dev-*/arms/*/trace.jsonl; do
  [ -e "$f" ] || continue
  gzip -9 -f "$f"
  echo "  gz  $f"
done

# Checksums over every artifact, compressed or not, so a later reader can prove nothing moved.
find run-dev-* *.json *.md *.py *.sh -type f 2>/dev/null \
  | grep -v __pycache__ | sort | xargs shasum -a 256 > SHA256SUMS
echo "  checksums: $(wc -l < SHA256SUMS | tr -d ' ') files -> SHA256SUMS"
