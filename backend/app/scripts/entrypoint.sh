#!/usr/bin/env bash
set -euo pipefail

# Boltz auto-downloads model weights + CCD on first predict run into $BOLTZ_CACHE.
# Mount a persistent volume at $BOLTZ_CACHE to avoid re-downloading on each container start.

exec "$@"
