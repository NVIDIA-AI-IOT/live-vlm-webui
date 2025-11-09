#!/bin/bash
# Script to run tests with different options

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
TEST_TYPE="all"
COVERAGE=false
VERBOSE=false
MARKERS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--unit)
            TEST_TYPE="unit"
            shift
            ;;
        -i|--integration)
            TEST_TYPE="integration"
            shift
            ;;
        -e|--e2e)
            TEST_TYPE="e2e"
            shift
            ;;
        -c|--coverage)
            COVERAGE=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -m|--markers)
            MARKERS="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -u, --unit          Run only unit tests"
            echo "  -i, --integration   Run only integration tests"
            echo "  -e, --e2e           Run only end-to-end tests"
            echo "  -c, --coverage      Run tests with coverage report"
            echo "  -v, --verbose       Verbose output"
            echo "  -m, --markers EXPR  Run tests matching marker expression"
            echo "  -h, --help          Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 -u              # Run unit tests"
            echo "  $0 -c              # Run all tests with coverage"
            echo "  $0 -m \"not slow\"   # Run tests without 'slow' marker"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Live VLM WebUI - Test Runner${NC}"
echo -e "${BLUE}════════════════════════════════════════════${NC}"
echo ""

# Build pytest command
PYTEST_CMD="pytest"

# Add test directory based on type
case $TEST_TYPE in
    unit)
        PYTEST_CMD="$PYTEST_CMD tests/unit"
        echo -e "${YELLOW}Running unit tests...${NC}"
        ;;
    integration)
        PYTEST_CMD="$PYTEST_CMD tests/integration"
        echo -e "${YELLOW}Running integration tests...${NC}"
        ;;
    e2e)
        PYTEST_CMD="$PYTEST_CMD tests/e2e"
        echo -e "${YELLOW}Running end-to-end tests...${NC}"
        ;;
    all)
        PYTEST_CMD="$PYTEST_CMD tests"
        echo -e "${YELLOW}Running all tests...${NC}"
        ;;
esac

# Add coverage if requested
if [ "$COVERAGE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD --cov=live_vlm_webui --cov-report=html --cov-report=term-missing"
    echo -e "${YELLOW}Coverage reporting enabled${NC}"
fi

# Add verbose if requested
if [ "$VERBOSE" = true ]; then
    PYTEST_CMD="$PYTEST_CMD -v"
fi

# Add markers if specified
if [ -n "$MARKERS" ]; then
    PYTEST_CMD="$PYTEST_CMD -m \"$MARKERS\""
fi

# Add other useful pytest flags
PYTEST_CMD="$PYTEST_CMD --tb=short --strict-markers"

echo ""
echo -e "${BLUE}Command: ${NC}$PYTEST_CMD"
echo ""

# Run tests
if eval $PYTEST_CMD; then
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════${NC}"
    echo -e "${GREEN}   ✓ All tests passed!${NC}"
    echo -e "${GREEN}════════════════════════════════════════════${NC}"

    if [ "$COVERAGE" = true ]; then
        echo ""
        echo -e "${BLUE}Coverage report saved to: htmlcov/index.html${NC}"
    fi

    exit 0
else
    echo ""
    echo -e "${RED}════════════════════════════════════════════${NC}"
    echo -e "${RED}   ✗ Tests failed${NC}"
    echo -e "${RED}════════════════════════════════════════════${NC}"
    exit 1
fi

