#!/bin/bash
# Generate comprehensive test coverage report

set -e

echo "🔍 Running tests with coverage..."
echo ""

# Run pytest with coverage
pytest tests/ \
    --cov=live_vlm_webui \
    --cov-report=html \
    --cov-report=term-missing \
    --cov-report=json \
    --cov-fail-under=0 \
    -v

echo ""
echo "✅ Coverage report generated!"
echo ""
echo "📊 View the HTML report: htmlcov/index.html"
echo "📄 JSON report saved to: coverage.json"
echo ""

# Check if coverage is below threshold
COVERAGE_THRESHOLD=70

# Extract coverage percentage from JSON (requires jq, or we can parse differently)
if command -v jq &> /dev/null; then
    COVERAGE=$(jq -r '.totals.percent_covered' coverage.json 2>/dev/null || echo "0")
    COVERAGE_INT=${COVERAGE%.*}

    echo "Total coverage: ${COVERAGE}%"

    if [ "$COVERAGE_INT" -lt "$COVERAGE_THRESHOLD" ]; then
        echo "⚠️  Warning: Coverage is below ${COVERAGE_THRESHOLD}%"
        exit 1
    else
        echo "✅ Coverage meets threshold (${COVERAGE_THRESHOLD}%)"
    fi
else
    echo "💡 Install jq for automatic coverage threshold checking:"
    echo "   sudo apt-get install jq"
fi

