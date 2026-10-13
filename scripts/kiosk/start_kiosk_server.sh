#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

# shellcheck source=/dev/null
source "$HOME/.config/live-vlm-webui/kiosk.env"
if [[ ${KIOSK_MODE:-single} == multi-stream ]]; then
    : "${KIOSK_REPO:?Set the repository checkout path}"
    : "${KIOSK_THOR_CONFIG:?Set the Thor configuration path}"
    kind=server
    case "${1:-}" in
        --backend) kind=backend ;;
        '') ;;
        *) echo "Unknown multi-stream service option: $1" >&2; exit 2 ;;
    esac
    exec python3.12 "$(dirname -- "${BASH_SOURCE[0]}")/configure_multistream.py" \
        --repo "$KIOSK_REPO" --config "$KIOSK_THOR_CONFIG" run --kind "$kind"
fi
if [[ $# != 0 ]]; then
    echo 'The backend launcher requires a multi-stream kiosk configuration.' >&2
    exit 2
fi
: "${KIOSK_SERVER_BIN:?Set the absolute path to live-vlm-webui}"
: "${KIOSK_PORT:?Set a fixed server port}"
: "${KIOSK_API_BASE:?Set the VLM API base URL}"
: "${KIOSK_MODEL:?Set a downloaded VLM model}"

# Both values are explicit so startup never falls back to a different service/model.
exec "$KIOSK_SERVER_BIN" --localhost-http --port "$KIOSK_PORT" \
    --api-base "$KIOSK_API_BASE" --model "$KIOSK_MODEL" \
    --prompt "${KIOSK_PROMPT:-Describe what you see in this image in one sentence.}" \
    --process-every "${KIOSK_PROCESS_EVERY:-30}" \
    --max-tokens "${KIOSK_MAX_TOKENS:-128}"
