#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Start the repository-installed Cosmos runtime; no borrowed benchmark directory.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
action=serve
args=()
while (($#)); do
    case "$1" in
        --models-dir)
            (($# >= 2)) || { printf '%s\n' '--models-dir requires a value' >&2; exit 1; }
            args+=(--work-dir "$2"); shift 2 ;;
        --check) action=check; shift ;;
        --help|-h)
            printf '%s\n' 'Usage: scripts/run_cosmos_edge_thor.sh [--models-dir WORK] [--runtime-prefix DIR]' \
              '       [--model-dir DIR] [--cache-dir DIR] [--image NAME] [--host ADDRESS]' \
              '       [--port PORT] [--name NAME] [--check] [--dry-run]' \
              'Prepare first: python3 scripts/setup_cosmos_edge_thor.py runtime' \
              'Then model-download (private HF access) or model-build (public source, GPU conversion).' \
              'First serve builds fresh target engines. See docs/development/cosmos-edge-demo.md.'
            exit 0 ;;
        *) args+=("$1"); shift ;;
    esac
done
exec python3 "${script_dir}/setup_cosmos_edge_thor.py" "$action" "${args[@]}"
