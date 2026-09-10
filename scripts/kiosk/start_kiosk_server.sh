#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

source "$HOME/.config/live-vlm-webui/kiosk.env"
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
