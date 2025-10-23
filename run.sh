#!/bin/bash
# Crypto Price LTP - Unified Start/Stop Script
# Usage: ./run.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# PID files
WEB_PID_FILE="$SCRIPT_DIR/.web_dashboard.pid"
MANAGER_PID_FILE="$SCRIPT_DIR/.manager.pid"

# Function to print colored messages
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }
print_info() { echo -e "${BLUE}ℹ${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "=========================================="
    echo "Shutting down Crypto Price LTP System..."
    echo "=========================================="

    # Kill web dashboard
    if [ -f "$WEB_PID_FILE" ]; then
        WEB_PID=$(cat "$WEB_PID_FILE")
        if ps -p "$WEB_PID" > /dev/null 2>&1; then
            print_info "Stopping Web Dashboard (PID: $WEB_PID)..."
            kill "$WEB_PID" 2>/dev/null || true
            sleep 1
            # Force kill if still running
            if ps -p "$WEB_PID" > /dev/null 2>&1; then
                kill -9 "$WEB_PID" 2>/dev/null || true
            fi
        fi
        rm -f "$WEB_PID_FILE"
    fi

    # Kill manager
    if [ -f "$MANAGER_PID_FILE" ]; then
        MANAGER_PID=$(cat "$MANAGER_PID_FILE")
        if ps -p "$MANAGER_PID" > /dev/null 2>&1; then
            print_info "Stopping Service Manager (PID: $MANAGER_PID)..."
            kill "$MANAGER_PID" 2>/dev/null || true
            sleep 1
            # Force kill if still running
            if ps -p "$MANAGER_PID" > /dev/null 2>&1; then
                kill -9 "$MANAGER_PID" 2>/dev/null || true
            fi
        fi
        rm -f "$MANAGER_PID_FILE"
    fi

    # Kill any remaining processes
    pkill -f "web_dashboard.py" 2>/dev/null || true
    pkill -f "manager.py" 2>/dev/null || true

    sleep 1
    print_success "System stopped successfully!"
    exit 0
}

# Trap Ctrl+C (SIGINT), Ctrl+Z followed by kill (SIGTERM), and other signals
trap cleanup SIGINT SIGTERM EXIT

echo "=========================================="
echo "  Crypto Price LTP - Control System"
echo "=========================================="
echo ""

# Check if Redis is running
print_info "Checking Redis connection..."
if ! redis-cli ping > /dev/null 2>&1; then
    print_error "Redis is not running!"
    echo ""
    echo "Please start Redis first:"
    echo "  macOS: brew services start redis"
    echo "  Linux: sudo systemctl start redis"
    echo "  Manual: redis-server"
    exit 1
fi
print_success "Redis is running"

# Clean up any existing processes
print_info "Cleaning up any existing processes..."
pkill -f "web_dashboard.py" 2>/dev/null || true
pkill -f "manager.py" 2>/dev/null || true
rm -f "$WEB_PID_FILE" "$MANAGER_PID_FILE"
sleep 1

# Start Web Dashboard
print_info "Starting Web Dashboard..."
python web_dashboard.py > logs/web_dashboard.log 2>&1 &
WEB_PID=$!
echo $WEB_PID > "$WEB_PID_FILE"
sleep 2

# Verify web dashboard started
if ! ps -p $WEB_PID > /dev/null 2>&1; then
    print_error "Failed to start Web Dashboard"
    cat logs/web_dashboard.log | tail -20
    exit 1
fi
print_success "Web Dashboard started (PID: $WEB_PID)"

# Start Service Manager
print_info "Starting Service Manager..."
python manager.py > logs/service_manager.log 2>&1 &
MANAGER_PID=$!
echo $MANAGER_PID > "$MANAGER_PID_FILE"
sleep 2

# Verify manager started
if ! ps -p $MANAGER_PID > /dev/null 2>&1; then
    print_error "Failed to start Service Manager"
    cat logs/service_manager.log | tail -20
    cleanup
    exit 1
fi
print_success "Service Manager started (PID: $MANAGER_PID)"

echo ""
echo "=========================================="
echo "  System Started Successfully!"
echo "=========================================="
echo ""
echo "  🌐 Web Dashboard: http://localhost:8080"
echo "  📚 API Docs: http://localhost:8080/docs"
echo ""
echo "  All services are in STOPPED state."
echo "  Open the dashboard to start services."
echo ""
echo "=========================================="
echo "  Press Ctrl+C to stop all services"
echo "=========================================="
echo ""

# Monitor logs in the foreground (this keeps the script running)
tail -f logs/service_manager.log 2>/dev/null &
TAIL_PID=$!

# Wait indefinitely (until Ctrl+C or kill signal)
wait $TAIL_PID 2>/dev/null || true

# Cleanup will be called automatically by trap
