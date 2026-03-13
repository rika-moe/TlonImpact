#!/bin/bash
#
# Tlon Integration Test Runner
#
# Usage:
#   ./run_tests.sh                    # Run all tests
#   ./run_tests.sh --test TC001,TC002 # Run specific tests
#   ./run_tests.sh --keep-server      # Keep server running after tests
#   ./run_tests.sh --mock-llm         # Use mock LLM responses
#
# Environment Variables:
#   TLON_TEST_HOST       - Server host (default: 127.0.0.1)
#   TLON_TEST_REST_PORT  - REST API port (default: 18080)
#   TLON_TEST_WS_PORT    - WebSocket port (default: 18888)
#   OPENAI_API_KEY       - Required for LLM calls
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Default configuration
HOST="${TLON_TEST_HOST:-127.0.0.1}"
REST_PORT="${TLON_TEST_REST_PORT:-18080}"
WS_PORT="${TLON_TEST_WS_PORT:-18888}"
DATA_DIR="${SCRIPT_DIR}/test_data"
LOGS_DIR="${SCRIPT_DIR}/logs"
REPORTS_DIR="${SCRIPT_DIR}/reports"
KEEP_SERVER=false
MOCK_LLM=false
SPECIFIC_TESTS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --test)
            SPECIFIC_TESTS="$2"
            shift 2
            ;;
        --keep-server)
            KEEP_SERVER=true
            shift
            ;;
        --mock-llm)
            MOCK_LLM=true
            shift
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --rest-port)
            REST_PORT="$2"
            shift 2
            ;;
        --ws-port)
            WS_PORT="$2"
            shift 2
            ;;
        --help)
            echo "Tlon Integration Test Runner"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --test <TC001,TC002>   Run specific test cases"
            echo "  --keep-server          Keep server running after tests"
            echo "  --mock-llm             Use mock LLM (not yet implemented)"
            echo "  --host <host>          Server host"
            echo "  --rest-port <port>     REST API port"
            echo "  --ws-port <port>       WebSocket port"
            echo "  --help                 Show this help"
            echo ""
            echo "Environment Variables:"
            echo "  TLON_TEST_HOST, TLON_TEST_REST_PORT, TLON_TEST_WS_PORT"
            echo "  OPENAI_API_KEY (required)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Print banner
print_banner() {
    echo -e "${BLUE}"
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║          🧪 Tlon Integration Test Suite                    ║"
    echo "║     A universe constructed of ideas, invading reality      ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check uv
    if ! command -v uv &> /dev/null; then
        log_error "uv not found. Please install: https://github.com/astral-sh/uv"
        exit 1
    fi
    log_success "uv found: $(uv --version)"

    # Check python
    if ! uv run python --version &> /dev/null; then
        log_error "Python not available via uv"
        exit 1
    fi
    log_success "Python available"

    # Check OpenAI API key
    if [[ -z "${OPENAI_API_KEY}" ]]; then
        log_warning "OPENAI_API_KEY not set. LLM tests may fail."
    else
        log_success "OPENAI_API_KEY is set"
    fi

    # Check ports are available
    if lsof -Pi :${REST_PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_error "Port ${REST_PORT} is already in use"
        exit 1
    fi
    if lsof -Pi :${WS_PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
        log_error "Port ${WS_PORT} is already in use"
        exit 1
    fi
    log_success "Ports ${REST_PORT} and ${WS_PORT} are available"
}

# Setup test environment
setup_environment() {
    log_info "Setting up test environment..."

    # Create directories
    mkdir -p "${DATA_DIR}"
    mkdir -p "${LOGS_DIR}"
    mkdir -p "${REPORTS_DIR}"

    # Create isolated config for testing
    TEST_CONFIG_DIR="${DATA_DIR}/config"
    mkdir -p "${TEST_CONFIG_DIR}/agents"
    mkdir -p "${TEST_CONFIG_DIR}/positions"

    # Copy minimal config for testing
    log_info "Copying test configuration..."
    cp -r "${PROJECT_ROOT}/config/positions/"*.yaml "${TEST_CONFIG_DIR}/positions/" 2>/dev/null || true

    # Copy only a subset of agents for faster testing
    AGENTS=("lin-zhiyao.yaml" "shen-qingyan.yaml" "gu-yanxi.yaml")
    for agent in "${AGENTS[@]}"; do
        if [[ -f "${PROJECT_ROOT}/config/agents/${agent}" ]]; then
            cp "${PROJECT_ROOT}/config/agents/${agent}" "${TEST_CONFIG_DIR}/agents/"
        fi
    done

    log_success "Test environment ready at ${DATA_DIR}"
}

# Start the server
start_server() {
    log_info "Starting Tlon Server..."
    log_info "  Host: ${HOST}"
    log_info "  REST Port: ${REST_PORT}"
    log_info "  WS Port: ${WS_PORT}"

    # Create log file
    SERVER_LOG="${LOGS_DIR}/server.log"

    # Start server in background
    cd "${PROJECT_ROOT}"
    uv run python -m tlon dream \
        --host "${HOST}" \
        --rest-port "${REST_PORT}" \
        --ws-port "${WS_PORT}" \
        > "${SERVER_LOG}" 2>&1 &

    SERVER_PID=$!
    log_info "Server started with PID: ${SERVER_PID}"

    # Wait for server to be ready
    log_info "Waiting for server to be ready..."
    MAX_RETRIES=30
    RETRY_COUNT=0

    while [[ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]]; do
        if curl -s "http://${HOST}:${REST_PORT}/health" > /dev/null 2>&1; then
            log_success "Server is ready!"
            return 0
        fi

        # Check if process is still running
        if ! kill -0 ${SERVER_PID} 2>/dev/null; then
            log_error "Server process died unexpectedly"
            log_error "Check logs: ${SERVER_LOG}"
            exit 1
        fi

        sleep 1
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo -n "."
    done

    echo ""
    log_error "Server failed to start within ${MAX_RETRIES} seconds"
    log_error "Check logs: ${SERVER_LOG}"
    exit 1
}

# Stop the server
stop_server() {
    if [[ -n "${SERVER_PID}" ]]; then
        log_info "Stopping server (PID: ${SERVER_PID})..."
        kill ${SERVER_PID} 2>/dev/null || true
        wait ${SERVER_PID} 2>/dev/null || true
        log_success "Server stopped"
    fi
}

# Run tests
run_tests() {
    log_info "Running integration tests..."

    REPORT_FILE="${REPORTS_DIR}/test_report_$(date +%Y%m%d_%H%M%S).json"

    # Build test arguments
    TEST_ARGS="--host ${HOST} --ws-port ${WS_PORT} --rest-port ${REST_PORT} --output ${REPORT_FILE}"

    if [[ -n "${SPECIFIC_TESTS}" ]]; then
        TEST_ARGS="${TEST_ARGS} --test ${SPECIFIC_TESTS}"
    fi

    # Run test client
    cd "${PROJECT_ROOT}"
    if uv run python "${SCRIPT_DIR}/test_client.py" ${TEST_ARGS}; then
        log_success "All tests passed!"
        TEST_EXIT_CODE=0
    else
        log_error "Some tests failed"
        TEST_EXIT_CODE=1
    fi

    # Print report summary
    if [[ -f "${REPORT_FILE}" ]]; then
        echo ""
        log_info "Test Report: ${REPORT_FILE}"

        # Pretty print summary if jq is available
        if command -v jq &> /dev/null; then
            echo ""
            jq -r '.summary | to_entries[] | "  \(.key): \(.value)"' "${REPORT_FILE}"
        fi
    fi

    return ${TEST_EXIT_CODE}
}

# Cleanup function
cleanup() {
    echo ""
    log_info "Cleaning up..."

    if [[ "${KEEP_SERVER}" == "false" ]]; then
        stop_server
    else
        log_info "Keeping server running (PID: ${SERVER_PID})"
        log_info "  REST API: http://${HOST}:${REST_PORT}"
        log_info "  WebSocket: ws://${HOST}:${WS_PORT}/ws"
        log_info "  Logs: ${LOGS_DIR}/server.log"
        log_info "To stop: kill ${SERVER_PID}"
    fi

    # Cleanup temp data unless KEEP_DATA is set
    if [[ -z "${KEEP_DATA}" ]]; then
        rm -rf "${DATA_DIR}"
        log_info "Test data cleaned up"
    fi
}

# Main execution
main() {
    print_banner

    # Set trap for cleanup
    trap cleanup EXIT

    # Run test phases
    check_prerequisites
    setup_environment
    start_server
    run_tests
    TEST_RESULT=$?

    # Print final status
    echo ""
    if [[ ${TEST_RESULT} -eq 0 ]]; then
        echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║                  🎉 ALL TESTS PASSED!                      ║${NC}"
        echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    else
        echo -e "${RED}╔════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║                  ❌ SOME TESTS FAILED                      ║${NC}"
        echo -e "${RED}╚════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        log_info "Check logs for details:"
        log_info "  Server: ${LOGS_DIR}/server.log"
    fi

    return ${TEST_RESULT}
}

# Run main
main "$@"
