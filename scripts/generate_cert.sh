#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Generate a development certificate for this machine, never a previous Jetson's IP.
# Optional: LIVE_VLM_CERT_HOST=192.168.1.50 ./scripts/generate_cert.sh
# Or pass multiple hostnames/IPs as separate arguments.
set -euo pipefail
umask 077

if [[ "${1:-}" == "--help" ]]; then
    printf 'Usage: %s [HOST_OR_IP ...]\n' "$0"
    printf 'Defaults to localhost; LIVE_VLM_CERT_HOST adds one host or IP.\n'
    exit 0
fi

certificate_hosts=("$@")
if [[ -n "${LIVE_VLM_CERT_HOST:-}" ]]; then
    certificate_hosts+=("${LIVE_VLM_CERT_HOST}")
fi
subject_alt_names="$(python3 - "${certificate_hosts[@]}" <<'PYHOSTS'
import ipaddress
import re
import sys

names = ["DNS:localhost", "IP:127.0.0.1", "IP:::1"]
for host in sys.argv[1:]:
    try:
        value = f"IP:{ipaddress.ip_address(host)}"
    except ValueError:
        labels = host.rstrip(".").split(".")
        if len(host) > 253 or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        ):
            raise SystemExit(f"Invalid certificate hostname or IP: {host!r}")
        value = f"DNS:{host}"
    if value not in names:
        names.append(value)
print(",".join(names))
PYHOSTS
)"

printf 'Generating self-signed development certificate for %s...\n' "${subject_alt_names}"
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem \
  -keyout key.pem \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=${subject_alt_names}"

printf 'Certificate generated: cert.pem and key.pem (private key).\n'
printf 'Your browser must trust this development certificate before camera access.\n'
