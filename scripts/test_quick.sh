#!/bin/bash
# Quick test run (unit tests only, no slow tests)

set -e

echo "⚡ Running quick tests (unit tests, excluding slow tests)..."
echo ""

pytest tests/unit \
    -v \
    -m "not slow" \
    --tb=short \
    --maxfail=3 \
    --ff \
    -x

echo ""
echo "✅ Quick tests completed!"

