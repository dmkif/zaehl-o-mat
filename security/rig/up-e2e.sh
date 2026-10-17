#!/usr/bin/env bash
# Start the security rig plus the HTTPS browser edge for the e2e suite (feature 013).
# Same knobs as up.sh; the frontend build is read from SEC_E2E_DIST (default <repo>/frontend/dist).
# rig.env then contains PROXY_URL (https://127.0.0.1:<port>), the origin the browser must use.
set -euo pipefail
RIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_E2E=1 exec "$RIG_DIR/up.sh" "$@"
